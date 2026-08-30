// Помощник захвата звука для «Расшифровки записей».
//
//   v2t-capture record <папка> [--video <файл.mp4>] [--app <bundle-id>]
//                                — пишет mic.pcm и sys.pcm (16 кГц, моно, int16 LE),
//                                  а с --video ещё и картинку: весь экран или
//                                  окно одного приложения
//   v2t-capture list-apps        — что сейчас запущено и может быть записано
//   v2t-capture mic <папка>      — только mic.pcm: встреча в комнате, где все
//                                  голоса и так идут в один микрофон
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

// MARK: - Запись картинки

/// Пишет кадры ScreenCaptureKit в mp4. Отдельно от звука: звук всё так же
/// уходит в pcm-дорожки, которые разбирает Whisper, — видео здесь только для
/// человека, чтобы вернуться к нужной минуте и увидеть, что показывали.
@available(macOS 13.0, *)
final class VideoWriter {
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private let path: String
    private let lock = NSLock()
    private var started = false
    /// Запись сорвалась: дальше принимать кадры бессмысленно, а файл придётся
    /// удалить — mp4 без индекса не открывается ничем.
    private var broken = false
    private var closing = false
    private var last = CMTime.zero
    private(set) var frames: Int64 = 0
    private(set) var dropped: Int64 = 0

    init(path: String, width: Int, height: Int) throws {
        self.path = path
        try? FileManager.default.removeItem(atPath: path)
        writer = try AVAssetWriter(outputURL: URL(fileURLWithPath: path), fileType: .mp4)
        let settings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [
                // Экран — не кино: битрейт скромный, зато час записи весит
                // сотни мегабайт, а не десятки гигабайт.
                AVVideoAverageBitRateKey: 2_500_000,
                AVVideoMaxKeyFrameIntervalKey: 60,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
            ],
        ]
        input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        input.expectsMediaDataInRealTime = true
        guard writer.canAdd(input) else { throw Failure("не добавить дорожку видео") }
        writer.add(input)
    }

    /// Кадр со временем от начала записи: метки в приложении считаются от той
    /// же точки, что и звук, иначе переход по метке уводил бы не туда.
    func append(_ sb: CMSampleBuffer, at seconds: Double) {
        lock.lock(); defer { lock.unlock() }
        if broken || closing { return }
        guard let image = CMSampleBufferGetImageBuffer(sb) else { return }
        if !started {
            guard writer.startWriting() else {
                broken = true
                note("видео: не начать запись — \(reason())")
                return
            }
            writer.startSession(atSourceTime: .zero)
            started = true
        }
        guard writer.status == .writing else {
            broken = true
            note("видео: запись сорвалась на кадре \(frames) — \(reason())")
            return
        }
        // Кодировщик не успевает — кадр пропускаем. Это нормально и не ошибка:
        // на экране обычно ничего не меняется.
        guard input.isReadyForMoreMediaData else { dropped += 1; return }

        // Время кадра обязано расти строго. ScreenCaptureKit присылает кадры
        // пачками, и два подряд легко попадают в одну шестисотую секунды —
        // а повторная метка времени роняет всю запись целиком: дальше
        // AVAssetWriter уходит в .failed и файл остаётся без индекса.
        var stamp = CMTime(seconds: max(0, seconds), preferredTimescale: 600)
        if frames > 0 && stamp <= last {
            stamp = CMTimeAdd(last, CMTime(value: 1, timescale: 600))
        }
        var timing = CMSampleTimingInfo(
            duration: .invalid,
            presentationTimeStamp: stamp,
            decodeTimeStamp: .invalid)
        var format: CMFormatDescription?
        CMVideoFormatDescriptionCreateForImageBuffer(allocator: kCFAllocatorDefault,
                                                     imageBuffer: image,
                                                     formatDescriptionOut: &format)
        guard let format else { return }
        var out: CMSampleBuffer?
        CMSampleBufferCreateReadyWithImageBuffer(allocator: kCFAllocatorDefault,
                                                 imageBuffer: image,
                                                 formatDescription: format,
                                                 sampleTiming: &timing,
                                                 sampleBufferOut: &out)
        guard let out else { return }
        // Отказ кодировщика раньше проходил молча: файл писался, индекс не
        // дописывался, и человек получал битый mp4 без единого сообщения.
        guard input.append(out) else {
            broken = true
            note("видео: кадр \(frames) не принят — \(reason())")
            return
        }
        last = stamp
        frames += 1
    }

    private func reason() -> String {
        writer.error.map { "\($0)" } ?? "причина неизвестна (status=\(writer.status.rawValue))"
    }

    /// Закрывает файл. Возвращает `true`, только если mp4 действительно готов:
    /// во всех остальных случаях файл удаляется — пустое место честнее, чем
    /// запись, которая не открывается.
    @discardableResult
    func finish() async -> Bool {
        lock.lock()
        closing = true
        let live = started && !broken && writer.status == .writing
        if live { input.markAsFinished() }
        let why = live ? "" : reason()
        lock.unlock()
        guard live else {
            note("видео: закрывать нечего (кадров \(frames)) — \(why)")
            try? FileManager.default.removeItem(atPath: path)
            return false
        }
        // Ждём именно завершения записи: без этого mp4 остаётся без индекса и
        // не открывается ничем.
        await withCheckedContinuation { (done: CheckedContinuation<Void, Never>) in
            writer.finishWriting { done.resume() }
        }
        guard writer.status == .completed else {
            note("видео: файл не закрылся — \(reason())")
            try? FileManager.default.removeItem(atPath: path)
            return false
        }
        return true
    }
}

// MARK: - Захват

@available(macOS 13.0, *)
final class Capture: NSObject, SCStreamOutput, SCStreamDelegate {
    private let sysWriter: TrackWriter
    private let micWriter: TrackWriter
    private var videoWriter: VideoWriter?
    private let videoPath: String?
    private let appBundleID: String?
    private let maxWidth: Int
    private let fps: Int
    private var stream: SCStream?
    private var started = Date()
    private var sysSeen = false
    private var micSeen = false

    init(directory: String, video: String? = nil, app: String? = nil,
         width: Int = 1600, fps: Int = 8) throws {
        sysWriter = try TrackWriter(path: directory + "/sys.pcm")
        micWriter = try TrackWriter(path: directory + "/mic.pcm")
        videoPath = video
        appBundleID = app
        maxWidth = max(320, width)
        self.fps = max(1, min(30, fps))
        super.init()
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false,
                                                                          onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            throw Failure("не найден экран для захвата")
        }

        let config = SCStreamConfiguration()
        if videoPath != nil {
            // Пишем картинку: ширину ограничиваем, кадров берём мало — экран
            // меняется редко, а размер файла растёт быстро.
            let scale = min(1.0, Double(maxWidth) / Double(display.width))
            // Кодировщику нужны чётные стороны.
            config.width = Int(Double(display.width) * scale) & ~1
            config.height = Int(Double(display.height) * scale) & ~1
            config.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
            config.showsCursor = true
            config.queueDepth = 6
            videoWriter = try VideoWriter(path: videoPath!,
                                          width: config.width, height: config.height)
        } else {
            // Видео нам не нужно, но ScreenCaptureKit без него не работает —
            // поэтому берём картинку минимального размера и почти без кадров.
            config.width = 2
            config.height = 2
            config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            config.showsCursor = false
        }
        config.capturesAudio = true
        config.sampleRate = 48000
        config.channelCount = 2
        config.excludesCurrentProcessAudio = true
        if #available(macOS 15.0, *) {
            config.captureMicrophone = true
        }

        // Весь экран или окна одного приложения. Звук при этом остаётся
        // системным целиком: собеседников слышно, даже когда показывают
        // только одно окно.
        var filter = SCContentFilter(display: display, excludingWindows: [])
        if let appBundleID,
           let app = content.applications.first(where: { $0.bundleIdentifier == appBundleID }) {
            filter = SCContentFilter(display: display, including: [app], exceptingWindows: [])
        }
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        let queue = DispatchQueue(label: "v2t.capture", qos: .userInitiated)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        if videoPath != nil {
            try stream.addStreamOutput(self, type: .screen,
                                       sampleHandlerQueue: DispatchQueue(label: "v2t.video",
                                                                         qos: .userInitiated))
        }
        if #available(macOS 15.0, *) {
            try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        }
        try await stream.startCapture()
        self.stream = stream
        self.started = Date()
        note("started")
    }

    func stop() async {
        // Сначала закрываем видео, потом останавливаем поток: если
        // stopCapture задумается или зависнет, помощника прибьют по таймауту —
        // и mp4 останется без индекса. Кадры, пришедшие в эту секунду, писать
        // уже некуда, и VideoWriter их отбрасывает сам.
        if let videoWriter {
            let ok = await videoWriter.finish()
            note("video frames=\(videoWriter.frames) skipped=\(videoWriter.dropped) ok=\(ok)")
        }
        if let stream { try? await stream.stopCapture() }
        sysWriter.close()
        micWriter.close()
        note("stopped sys=\(sysWriter.frames) mic=\(micWriter.frames)")
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard sampleBuffer.isValid else { return }
        let elapsed = Date().timeIntervalSince(started)

        if type == .screen {
            // Кадры без изменений ScreenCaptureKit присылает пустыми — такие
            // писать незачем, они только раздувают файл.
            if let videoWriter, isComplete(sampleBuffer) {
                videoWriter.append(sampleBuffer, at: elapsed)
            }
            return
        }
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

    /// Полноценный ли это кадр: ScreenCaptureKit метит пустые и повторные.
    private func isComplete(_ sb: CMSampleBuffer) -> Bool {
        guard let attachments = CMSampleBufferGetSampleAttachmentsArray(
                sb, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let raw = attachments.first?[.status] as? Int,
              let status = SCFrameStatus(rawValue: raw) else { return false }
        return status == .complete
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

// MARK: - Только микрофон (встреча в комнате)

/// Для встречи за столом системный звук не нужен: все голоса приходят в один
/// микрофон. Поэтому здесь не ScreenCaptureKit, а обычный аудиодвижок — и
/// разрешение нужно только на микрофон, без «записи экрана».
@available(macOS 13.0, *)
final class MicCapture {
    private let writer: TrackWriter
    private let engine = AVAudioEngine()

    init(directory: String) throws {
        writer = try TrackWriter(path: directory + "/mic.pcm")
    }

    func start() throws {
        let input = engine.inputNode
        let format = input.inputFormat(forBus: 0)
        guard format.sampleRate > 0 else {
            throw Failure("микрофон не отдаёт звук — проверьте устройство ввода")
        }
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            self?.writer.append(buffer)
        }
        engine.prepare()
        try engine.start()
        note("mic started at \(Int(format.sampleRate)) Hz")
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        writer.close()
        note("mic stopped frames=\(writer.frames)")
    }
}

/// Спрашивает доступ к микрофону и ждёт ответа: без него движок стартует,
/// но пишет тишину, и это выясняется только в конце встречи.
func ensureMicrophone() -> Bool {
    let status = AVCaptureDevice.authorizationStatus(for: .audio)
    if status == .authorized { return true }
    if status == .denied || status == .restricted { return false }
    let done = DispatchSemaphore(value: 0)
    var granted = false
    AVCaptureDevice.requestAccess(for: .audio) { ok in granted = ok; done.signal() }
    _ = done.wait(timeout: .now() + 60)
    return granted
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

/// Строка для JSON: имена приложений бывают с кавычками и юникодом.
func quote(_ text: String) -> String {
    let escaped = text
        .replacingOccurrences(of: "\\", with: "\\\\")
        .replacingOccurrences(of: "\"", with: "\\\"")
        .replacingOccurrences(of: "\n", with: " ")
    return "\"" + escaped + "\""
}

/// Держим источники сигналов живыми: без ссылки их приберёт сборщик
/// и остановка по Ctrl-C перестанет работать.
var signalSources: [DispatchSourceSignal] = []

let args = CommandLine.arguments
let command = args.count > 1 ? args[1] : "check"

switch command {
case "list-apps":
    guard #available(macOS 13.0, *) else { print("[]"); exit(0) }
    let done = DispatchSemaphore(value: 0)
    Task {
        var rows: [String] = []
        if let content = try? await SCShareableContent.excludingDesktopWindows(
            true, onScreenWindowsOnly: true) {
            // Только то, у чего есть окно на экране: записывать фоновую службу
            // человеку незачем, а список из двухсот строк бесполезен.
            let visible = Set(content.windows.compactMap { $0.owningApplication?.bundleIdentifier })
            var seen = Set<String>()
            for app in content.applications
                where visible.contains(app.bundleIdentifier) && !app.applicationName.isEmpty {
                if seen.contains(app.bundleIdentifier) { continue }
                seen.insert(app.bundleIdentifier)
                rows.append("{\"id\":\(quote(app.bundleIdentifier)),"
                            + "\"name\":\(quote(app.applicationName))}")
            }
        }
        print("[" + rows.sorted().joined(separator: ",") + "]")
        done.signal()
    }
    _ = done.wait(timeout: .now() + 20)
    exit(0)

case "mic-status":
    print(micIsBusy() ? "1" : "0")

case "check":
    print(permissionsJSON())

case "request":
    requestPermissions()
    print(permissionsJSON())

case "mic":
    guard args.count > 2 else {
        note("нужна папка для записи"); exit(2)
    }
    guard #available(macOS 13.0, *) else {
        note("нужна macOS 13 или новее"); exit(2)
    }
    guard ensureMicrophone() else {
        note("нет доступа к микрофону"); exit(4)
    }
    let micDirectory = args[2]
    try? FileManager.default.createDirectory(atPath: micDirectory,
                                             withIntermediateDirectories: true)
    let micCapture: MicCapture
    do { micCapture = try MicCapture(directory: micDirectory) }
    catch { note("не начать запись: \(error)"); exit(2) }
    do { try micCapture.start() }
    catch { note("не начать запись с микрофона: \(error)"); exit(3) }

    let micStopping = DispatchSemaphore(value: 0)
    for sig in [SIGINT, SIGTERM] {
        signal(sig, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
        source.setEventHandler { micStopping.signal() }
        source.resume()
        signalSources.append(source)
    }
    DispatchQueue.global().async {
        micStopping.wait()
        micCapture.stop()
        exit(0)
    }
    RunLoop.main.run()

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
    // Ключи: --video <файл.mp4> и --app <bundle-id>. Без них всё как раньше —
    // только звук, и старые вызовы продолжают работать.
    var videoFile: String?
    var appID: String?
    var index = 3
    while index < args.count {
        switch args[index] {
        case "--video" where index + 1 < args.count:
            videoFile = args[index + 1]; index += 2
        case "--app" where index + 1 < args.count:
            appID = args[index + 1]; index += 2
        default:
            index += 1
        }
    }
    let capture: Capture
    do { capture = try Capture(directory: directory, video: videoFile, app: appID) }
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
