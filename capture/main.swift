// Помощник захвата звука для «Расшифровки записей».
//
//   v2t-capture record <папка>   — пишет mic.pcm и sys.pcm (16 кГц, моно, int16 LE)
//   v2t-capture mic-status       — «1», если микрофон кем-то занят (идёт созвон)
//   v2t-capture check            — состояние разрешений, одной строкой JSON
//
// Звук собеседников берётся через ScreenCaptureKit — штатный способ Apple,
// без сторонних аудиодрайверов. Свой голос идёт отдельной дорожкой: так
// «я» и «они» разделяются точно, без догадок по голосовым отпечаткам.

import AVFoundation
import CoreAudio
import Foundation
import ScreenCaptureKit

// MARK: - Запись дорожки в файл

/// Приводит поток любого формата к 16 кГц моно int16 и дописывает в файл.
final class TrackWriter {
    private let handle: FileHandle
    private let target: AVAudioFormat
    private var converter: AVAudioConverter?
    private var sourceFormat: AVAudioFormat?
    private let lock = NSLock()
    private(set) var frames: Int64 = 0

    init(path: String) throws {
        FileManager.default.createFile(atPath: path, contents: nil)
        guard let h = FileHandle(forWritingAtPath: path) else {
            throw Failure("не удалось открыть на запись: \(path)")
        }
        handle = h
        guard let t = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                    sampleRate: 16000, channels: 1, interleaved: true) else {
            throw Failure("не создать целевой формат")
        }
        target = t
    }

    func append(_ input: AVAudioPCMBuffer) {
        lock.lock(); defer { lock.unlock() }

        if sourceFormat != input.format {
            sourceFormat = input.format
            converter = AVAudioConverter(from: input.format, to: target)
            converter?.sampleRateConverterQuality = AVAudioQuality.medium.rawValue
        }
        guard let converter else { return }

        let ratio = target.sampleRate / input.format.sampleRate
        let capacity = AVAudioFrameCount(Double(input.frameLength) * ratio) + 1024
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }

        var supplied = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if supplied {
                status.pointee = .noDataNow
                return nil
            }
            supplied = true
            status.pointee = .haveData
            return input
        }
        if error != nil || out.frameLength == 0 { return }

        let bytes = Int(out.frameLength) * 2
        if let channel = out.int16ChannelData {
            handle.write(Data(bytes: channel[0], count: bytes))
            frames += Int64(out.frameLength)
        }
    }

    /// Тишина нужна, когда одна дорожка началась позже другой: без неё
    /// дорожки разъедутся по времени и реплики встанут не на свои места.
    func padSilence(seconds: Double) {
        guard seconds > 0 else { return }
        lock.lock(); defer { lock.unlock() }
        let count = Int(seconds * 16000)
        handle.write(Data(count: count * 2))
        frames += Int64(count)
    }

    func close() {
        lock.lock(); defer { lock.unlock() }
        try? handle.close()
    }
}

struct Failure: Error, CustomStringConvertible {
    let description: String
    init(_ text: String) { description = text }
}

// MARK: - Захват

@available(macOS 13.0, *)
final class Capture: NSObject, SCStreamOutput, SCStreamDelegate {
    private let sysWriter: TrackWriter
    private let micWriter: TrackWriter
    private var stream: SCStream?
    private var started = Date()
    private var sysSeen = false
    private var micSeen = false

    init(directory: String) throws {
        sysWriter = try TrackWriter(path: directory + "/sys.pcm")
        micWriter = try TrackWriter(path: directory + "/mic.pcm")
        super.init()
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false,
                                                                          onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            throw Failure("не найден экран для захвата")
        }

        let config = SCStreamConfiguration()
        // Видео нам не нужно, но ScreenCaptureKit без него не работает —
        // поэтому берём картинку минимального размера и почти без кадров.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.showsCursor = false
        config.capturesAudio = true
        config.sampleRate = 48000
        config.channelCount = 2
        config.excludesCurrentProcessAudio = true
        if #available(macOS 15.0, *) {
            config.captureMicrophone = true
        }

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        let queue = DispatchQueue(label: "v2t.capture", qos: .userInitiated)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        if #available(macOS 15.0, *) {
            try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        }
        try await stream.startCapture()
        self.stream = stream
        self.started = Date()
        note("started")
    }

    func stop() async {
        if let stream { try? await stream.stopCapture() }
        sysWriter.close()
        micWriter.close()
        note("stopped sys=\(sysWriter.frames) mic=\(micWriter.frames)")
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard sampleBuffer.isValid else { return }
        let elapsed = Date().timeIntervalSince(started)

        if type == .audio {
            if !sysSeen { sysSeen = true; sysWriter.padSilence(seconds: elapsed) }
            write(sampleBuffer, to: sysWriter)
            return
        }
        if #available(macOS 15.0, *), type == .microphone {
            if !micSeen { micSeen = true; micWriter.padSilence(seconds: elapsed) }
            write(sampleBuffer, to: micWriter)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        note("error \(error.localizedDescription)")
        exit(3)
    }

    private func write(_ sb: CMSampleBuffer, to writer: TrackWriter) {
        guard let desc = CMSampleBufferGetFormatDescription(sb),
              let asbdPtr = CMAudioFormatDescriptionGetStreamBasicDescription(desc) else { return }
        var asbd = asbdPtr.pointee
        guard let format = AVAudioFormat(streamDescription: &asbd) else { return }
        try? sb.withAudioBufferList { list, _ in
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                                bufferListNoCopy: list.unsafePointer) else { return }
            writer.append(buffer)
        }
    }
}

// MARK: - Занят ли микрофон

/// Спрашивает у Core Audio, использует ли кто-нибудь устройство ввода.
/// Это и есть признак идущего созвона — надёжнее, чем смотреть на список
/// запущенных программ.
func micIsBusy() -> Bool {
    var deviceID = AudioObjectID(0)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultInputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),
                                     &address, 0, nil, &size, &deviceID) == noErr,
          deviceID != 0 else { return false }

    var running = UInt32(0)
    var runningSize = UInt32(MemoryLayout<UInt32>.size)
    var runningAddress = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceIsRunningSomewhere,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    guard AudioObjectGetPropertyData(deviceID, &runningAddress, 0, nil,
                                     &runningSize, &running) == noErr else { return false }
    return running != 0
}

// MARK: - Разрешения

func permissionsJSON() -> String {
    var screen = false
    if #available(macOS 11.0, *) { screen = CGPreflightScreenCaptureAccess() }
    let mic = AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
    return "{\"screen\": \(screen), \"microphone\": \(mic)}"
}

func requestPermissions() {
    if #available(macOS 11.0, *), !CGPreflightScreenCaptureAccess() {
        CGRequestScreenCaptureAccess()
    }
    if AVCaptureDevice.authorizationStatus(for: .audio) != .authorized {
        let done = DispatchSemaphore(value: 0)
        AVCaptureDevice.requestAccess(for: .audio) { _ in done.signal() }
        _ = done.wait(timeout: .now() + 60)
    }
}

// MARK: - Точка входа

func note(_ text: String) {
    FileHandle.standardError.write(Data(("[v2t-capture] " + text + "\n").utf8))
}

/// Держим источники сигналов живыми: без ссылки их приберёт сборщик
/// и остановка по Ctrl-C перестанет работать.
var signalSources: [DispatchSourceSignal] = []

let args = CommandLine.arguments
let command = args.count > 1 ? args[1] : "check"

switch command {
case "mic-status":
    print(micIsBusy() ? "1" : "0")

case "check":
    print(permissionsJSON())

case "request":
    requestPermissions()
    print(permissionsJSON())

case "record":
    guard args.count > 2 else {
        note("нужна папка для записи"); exit(2)
    }
    guard #available(macOS 13.0, *) else {
        note("нужна macOS 13 или новее"); exit(2)
    }
    let directory = args[2]
    try? FileManager.default.createDirectory(atPath: directory,
                                             withIntermediateDirectories: true)
    let capture: Capture
    do { capture = try Capture(directory: directory) }
    catch { note("не начать запись: \(error)"); exit(2) }

    // Останавливаемся по сигналу, чтобы файлы закрылись аккуратно.
    let stopping = DispatchSemaphore(value: 0)
    for sig in [SIGINT, SIGTERM] {
        signal(sig, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
        source.setEventHandler { stopping.signal() }
        source.resume()
        signalSources.append(source)
    }

    Task {
        do { try await capture.start() }
        catch { note("не начать захват: \(error)"); exit(3) }
    }

    DispatchQueue.global().async {
        stopping.wait()
        Task {
            await capture.stop()
            exit(0)
        }
    }
    RunLoop.main.run()

default:
    note("неизвестная команда: \(command)")
    exit(2)
}
