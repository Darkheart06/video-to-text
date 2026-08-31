// Помощник захвата звука для «Расшифровки записей».
//
//   v2t-capture record <папка> [--video <файл.mp4>] [--app <bundle-id>]
//                                — пишет mic.pcm и sys.pcm (16 кГц, моно, int16 LE),
//                                  а с --video ещё и картинку: весь экран или
//                                  окно одного приложения
//   v2t-capture list-apps [--shots]
//                                — что сейчас запущено и может быть записано;
//                                  с --shots ещё и картинка каждого источника,
//                                  чтобы выбирать глазами, а не по названию
//   v2t-capture mic <папка>      — только mic.pcm: встреча в комнате, где все
//                                  голоса и так идут в один микрофон
//   v2t-capture mic-status       — «1», если микрофон кем-то занят (идёт созвон)
//   v2t-capture check            — состояние разрешений, одной строкой JSON
//   v2t-capture calendar-status  — доступ к календарю: есть, нет, можно ли писать
//   v2t-capture calendar-request — спросить доступ у системы
//   v2t-capture calendars        — список календарей, куда можно писать
//   v2t-capture calendar <дней>  — события на ближайшие дни, списком JSON
//   v2t-capture calendar-add <json>
//                                — завести событие в календаре
//
// Звук собеседников берётся через ScreenCaptureKit — штатный способ Apple,
// без сторонних аудиодрайверов. Свой голос идёт отдельной дорожкой: так
// «я» и «они» разделяются точно, без догадок по голосовым отпечаткам.

import AVFoundation
import CoreAudio
import EventKit
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

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
    /// С какой секунды записи начался этот кусок: включённая посреди созвона
    /// картинка обязана начинаться со своего нуля, иначе в начале файла
    /// окажется полчаса пустоты.
    private let base: Double
    private(set) var frames: Int64 = 0
    private(set) var dropped: Int64 = 0
    let width: Int
    let height: Int

    init(path: String, width: Int, height: Int, base: Double = 0) throws {
        self.path = path
        self.base = base
        self.width = width
        self.height = height
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
        var stamp = CMTime(seconds: max(0, seconds - base), preferredTimescale: 600)
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
    private var appBundleID: String?
    private let maxWidth: Int
    private let fps: Int
    private var stream: SCStream?
    private var display: SCDisplay?
    private var started = Date()
    private var sysSeen = false
    private var micSeen = false
    /// Идёт ли сейчас запись картинки. Её включают и выключают посреди
    /// созвона: экран показывают не весь разговор, а десять минут из часа.
    private(set) var videoOn = false

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

    /// Настройки потока. Картинка либо настоящая, либо заглушка 2×2: без
    /// видео ScreenCaptureKit не работает вовсе, а платить за него полным
    /// экраном, когда пишут только звук, незачем.
    private func makeConfig(video: Bool, display: SCDisplay) -> SCStreamConfiguration {
        let config = SCStreamConfiguration()
        if video {
            // Пишем картинку: ширину ограничиваем, кадров берём мало — экран
            // меняется редко, а размер файла растёт быстро.
            let scale = min(1.0, Double(maxWidth) / Double(display.width))
            // Кодировщику нужны чётные стороны.
            config.width = Int(Double(display.width) * scale) & ~1
            config.height = Int(Double(display.height) * scale) & ~1
            config.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
            config.showsCursor = true
            config.queueDepth = 6
        } else {
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
        return config
    }

    /// Весь экран или окна одного приложения. Звук при этом остаётся
    /// системным целиком: собеседников слышно, даже когда показывают
    /// только одно окно.
    private func makeFilter(content: SCShareableContent, display: SCDisplay,
                            app: String?) -> SCContentFilter {
        if let app, !app.isEmpty,
           let match = content.applications.first(where: { $0.bundleIdentifier == app }) {
            return SCContentFilter(display: display, including: [match], exceptingWindows: [])
        }
        return SCContentFilter(display: display, excludingWindows: [])
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false,
                                                                          onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            throw Failure("не найден экран для захвата")
        }
        self.display = display

        let wantsVideo = videoPath != nil
        let config = makeConfig(video: wantsVideo, display: display)
        if let videoPath {
            videoWriter = try VideoWriter(path: videoPath,
                                          width: config.width, height: config.height)
            videoOn = true
        }
        let filter = makeFilter(content: content, display: display, app: appBundleID)
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        let queue = DispatchQueue(label: "v2t.capture", qos: .userInitiated)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        // Дорожку кадров подключаем всегда, даже когда картинку не пишем:
        // включить её посреди созвона иначе было бы нечем, а пустые кадры
        // 2×2 раз в секунду ничего не стоят.
        try stream.addStreamOutput(self, type: .screen,
                                   sampleHandlerQueue: DispatchQueue(label: "v2t.video",
                                                                     qos: .userInitiated))
        if #available(macOS 15.0, *) {
            try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        }
        try await stream.startCapture()
        self.stream = stream
        self.started = Date()
        note("started")
    }

    /// Включает запись картинки посреди созвона. Файл свой на каждый кусок:
    /// экран показывают не весь разговор, и склеивать дырявую дорожку с
    /// непрерывным звуком — верный способ развалить метки.
    @available(macOS 14.0, *)
    func startVideo(path: String, app: String?) async -> String {
        guard let stream else { return "запись не идёт" }
        if videoWriter != nil { return "" }
        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(
                false, onScreenWindowsOnly: false)
        } catch { return "не получить список окон: \(error)" }
        guard let display = content.displays.first else { return "не найден экран" }
        self.display = display
        let config = makeConfig(video: true, display: display)
        do {
            let writer = try VideoWriter(path: path, width: config.width,
                                         height: config.height,
                                         base: Date().timeIntervalSince(started))
            try await stream.updateContentFilter(makeFilter(content: content,
                                                            display: display, app: app))
            try await stream.updateConfiguration(config)
            appBundleID = app
            videoWriter = writer
            videoOn = true
            note("video on \(path)")
            return ""
        } catch {
            note("видео не включилось: \(error)")
            return "\(error)"
        }
    }

    /// Выключает картинку и закрывает её файл. Звук при этом не прерывается:
    /// поток тот же, меняется только его настройка.
    func stopVideo() async -> String {
        guard let writer = videoWriter else { return "" }
        videoWriter = nil
        videoOn = false
        let ok = await writer.finish()
        note("video off frames=\(writer.frames) skipped=\(writer.dropped) ok=\(ok)")
        if #available(macOS 14.0, *), let stream, let display {
            do { try await stream.updateConfiguration(makeConfig(video: false, display: display)) }
            catch { note("поток не вернулся к звуку: \(error)") }
        }
        return ok ? "" : "видео не сохранилось"
    }

    func stop() async {
        // Сначала закрываем видео, потом останавливаем поток: если
        // stopCapture задумается или зависнет, помощника прибьют по таймауту —
        // и mp4 останется без индекса. Кадры, пришедшие в эту секунду, писать
        // уже некуда, и VideoWriter их отбрасывает сам.
        if let videoWriter {
            let ok = await videoWriter.finish()
            note("video frames=\(videoWriter.frames) skipped=\(videoWriter.dropped) ok=\(ok)")
            self.videoWriter = nil
            videoOn = false
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
            guard let videoWriter, isComplete(sampleBuffer) else { return }
            // Картинку включают посреди созвона, и первые мгновения после
            // переключения ещё летят кадры прежнего размера — заглушка 2×2.
            // Такой кадр в файл другого размера класть нельзя.
            if let image = CMSampleBufferGetImageBuffer(sampleBuffer) {
                let width = CVPixelBufferGetWidth(image)
                let height = CVPixelBufferGetHeight(image)
                if width != videoWriter.width || height != videoWriter.height { return }
            }
            videoWriter.append(sampleBuffer, at: elapsed)
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

/// Снимок источника для окна выбора: маленький jpeg строкой data-URI.
///
/// Выбирать, что записывать, по названию приложения неудобно — открытых окон
/// у одного и того же приложения бывает несколько, и человек не помнит, где
/// что. Поэтому показываем картинку, как это делают Зум и Телемост.
///
/// Снимок берётся штатным `SCScreenshotManager` (macOS 14+). На macOS 13 его
/// нет — тогда возвращаем пустую строку, и окно рисует плитку со значком:
/// выбор остаётся рабочим, просто без картинки.
@available(macOS 13.0, *)
func preview(_ filter: SCContentFilter, width: Int = 400) async -> String? {
    guard #available(macOS 14.0, *) else { return nil }
    let config = SCStreamConfiguration()
    let side = filter.contentRect
    let scale = min(1.0, Double(width) / max(1.0, side.width))
    config.width = max(2, Int(side.width * scale)) & ~1
    config.height = max(2, Int(side.height * scale)) & ~1
    config.showsCursor = false
    guard let image = try? await SCScreenshotManager.captureImage(
        contentFilter: filter, configuration: config) else { return nil }
    guard let data = jpeg(image) else { return nil }
    return "data:image/jpeg;base64," + data.base64EncodedString()
}

/// CGImage → jpeg. Через ImageIO, чтобы не тянуть в помощник весь AppKit.
func jpeg(_ image: CGImage, quality: Double = 0.55) -> Data? {
    let out = NSMutableData()
    guard let sink = CGImageDestinationCreateWithData(
        out, UTType.jpeg.identifier as CFString, 1, nil) else { return nil }
    CGImageDestinationAddImage(sink, image, [
        kCGImageDestinationLossyCompressionQuality: quality,
    ] as CFDictionary)
    guard CGImageDestinationFinalize(sink) else { return nil }
    return out as Data
}

// MARK: - Календарь

/// Доступ к календарю и события из него.
///
/// Читаем системный Календарь macOS, а не API сервисов: Gmail, Outlook и Яндекс
/// уже синхронизируются сюда — Google и Outlook штатно, Яндекс по CalDAV, — и
/// одно системное разрешение заменяет три отдельные интеграции с ревью,
/// токенами и постоянным сопровождением. Заодно ничего не уходит с машины.
let store = EKEventStore()

/// Спрашивать полный доступ или довольствоваться чтением — зависит от системы.
func askCalendar(_ done: @escaping (Bool, String) -> Void) {
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { ok, error in
            done(ok, error.map { "\($0)" } ?? "")
        }
    } else {
        store.requestAccess(to: .event) { ok, error in
            done(ok, error.map { "\($0)" } ?? "")
        }
    }
}

func calendarGranted() -> Bool {
    let status = EKEventStore.authorizationStatus(for: .event)
    if #available(macOS 14.0, *) {
        return status == .fullAccess || status == .writeOnly
    }
    return status == .authorized
}

/// Можно ли заводить события: на «только чтение» кнопка «Добавить» врала бы.
func calendarWritable() -> Bool {
    if #available(macOS 14.0, *) {
        let status = EKEventStore.authorizationStatus(for: .event)
        return status == .fullAccess || status == .writeOnly
    }
    return EKEventStore.authorizationStatus(for: .event) == .authorized
}

func calendarStatusJSON() -> String {
    let status = EKEventStore.authorizationStatus(for: .event).rawValue
    return "{\"granted\":\(calendarGranted()),\"writable\":\(calendarWritable()),"
         + "\"status\":\(status)}"
}

func calendarsJSON() -> String {
    let rows = store.calendars(for: .event).map { calendar -> String in
        "{\"id\":\(quote(calendar.calendarIdentifier)),"
        + "\"title\":\(quote(calendar.title)),"
        + "\"account\":\(quote(calendar.source?.title ?? "")),"
        + "\"writable\":\(calendar.allowsContentModifications)}"
    }
    return "[" + rows.joined(separator: ",") + "]"
}

/// События на ближайшие дни. Время — секундами эпохи: так между Swift, Python и
/// окном не теряются ни часовой пояс, ни переход на летнее время.
func eventsJSON(days: Int) -> String {
    let now = Date()
    let ahead = Calendar.current.date(byAdding: .day, value: max(1, days), to: now) ?? now
    // Немного назад: созвон, начавшийся десять минут назад, ещё идёт, и в
    // расписании он нужнее, чем завтрашний.
    let since = now.addingTimeInterval(-3 * 3600)
    let predicate = store.predicateForEvents(withStart: since, end: ahead, calendars: nil)
    let rows = store.events(matching: predicate).map { event -> String in
        let attendees = (event.attendees ?? []).map { person -> String in
            quote(person.name ?? person.url.absoluteString
                .replacingOccurrences(of: "mailto:", with: ""))
        }
        return "{\"id\":\(quote(event.eventIdentifier ?? "")),"
            + "\"title\":\(quote(event.title ?? "")),"
            + "\"start\":\(event.startDate?.timeIntervalSince1970 ?? 0),"
            + "\"end\":\(event.endDate?.timeIntervalSince1970 ?? 0),"
            + "\"allday\":\(event.isAllDay),"
            + "\"calendar\":\(quote(event.calendar?.title ?? "")),"
            + "\"account\":\(quote(event.calendar?.source?.title ?? "")),"
            + "\"where\":\(quote(event.location ?? "")),"
            + "\"url\":\(quote(event.url?.absoluteString ?? "")),"
            + "\"notes\":\(quote(String((event.notes ?? "").prefix(600)))),"
            + "\"organizer\":\(quote(event.organizer?.name ?? "")),"
            + "\"people\":[" + attendees.joined(separator: ",") + "]}"
    }
    return "[" + rows.joined(separator: ",") + "]"
}

/// Заводит событие. Разбираем вход руками, без Codable: полей пять, а лишняя
/// зависимость в помощнике ни к чему.
func addEvent(_ payload: String) -> String {
    guard let data = payload.data(using: .utf8),
          let raw = try? JSONSerialization.jsonObject(with: data),
          let dict = raw as? [String: Any] else {
        return "{\"ok\":false,\"error\":\"не разобрать событие\"}"
    }
    let event = EKEvent(eventStore: store)
    event.title = (dict["title"] as? String) ?? "Созвон"
    event.startDate = Date(timeIntervalSince1970: (dict["start"] as? Double) ?? 0)
    event.endDate = Date(timeIntervalSince1970: (dict["end"] as? Double) ?? 0)
    if let note = dict["notes"] as? String, !note.isEmpty { event.notes = note }
    if let place = dict["where"] as? String, !place.isEmpty { event.location = place }
    if let link = dict["url"] as? String, !link.isEmpty { event.url = URL(string: link) }

    let wanted = (dict["calendar"] as? String) ?? ""
    let writable = store.calendars(for: .event).filter { $0.allowsContentModifications }
    event.calendar = writable.first(where: { $0.calendarIdentifier == wanted || $0.title == wanted })
        ?? store.defaultCalendarForNewEvents
        ?? writable.first
    guard event.calendar != nil else {
        return "{\"ok\":false,\"error\":\"нет календаря, куда можно писать\"}"
    }
    do {
        try store.save(event, span: .thisEvent, commit: true)
        return "{\"ok\":true,\"id\":\(quote(event.eventIdentifier ?? "")),"
             + "\"calendar\":\(quote(event.calendar.title))}"
    } catch {
        return "{\"ok\":false,\"error\":\(quote("\(error)"))}"
    }
}

/// Держим источники сигналов живыми: без ссылки их приберёт сборщик
/// и остановка по Ctrl-C перестанет работать.
var signalSources: [DispatchSourceSignal] = []

let args = CommandLine.arguments
let command = args.count > 1 ? args[1] : "check"

switch command {
case "list-apps":
    guard #available(macOS 13.0, *) else { print("[]"); exit(0) }
    let wantShots = args.contains("--shots")
    let done = DispatchSemaphore(value: 0)
    Task {
        var rows: [String] = []
        if let content = try? await SCShareableContent.excludingDesktopWindows(
            true, onScreenWindowsOnly: true) {
            // Весь экран идёт первой плиткой: чаще всего показывают именно его.
            if let display = content.displays.first {
                let filter = SCContentFilter(display: display, excludingWindows: [])
                let shot = wantShots ? await preview(filter) : nil
                rows.append("{\"id\":\"screen\",\"name\":\"\",\"kind\":\"screen\","
                            + "\"width\":\(display.width),\"height\":\(display.height),"
                            + "\"shot\":\(quote(shot ?? ""))}")
            }
            // Только то, у чего есть окно на экране: записывать фоновую службу
            // человеку незачем, а список из двухсот строк бесполезен.
            let visible = Set(content.windows.compactMap { $0.owningApplication?.bundleIdentifier })
            var seen = Set<String>()
            var apps: [String] = []
            for app in content.applications
                where visible.contains(app.bundleIdentifier) && !app.applicationName.isEmpty {
                if seen.contains(app.bundleIdentifier) { continue }
                seen.insert(app.bundleIdentifier)
                var shot: String? = nil
                if wantShots, let display = content.displays.first {
                    shot = await preview(SCContentFilter(display: display, including: [app],
                                                         exceptingWindows: []))
                }
                apps.append("{\"id\":\(quote(app.bundleIdentifier)),"
                            + "\"name\":\(quote(app.applicationName)),"
                            + "\"kind\":\"app\",\"shot\":\(quote(shot ?? ""))}")
            }
            rows += apps.sorted()
        }
        print("[" + rows.joined(separator: ",") + "]")
        done.signal()
    }
    // Снимки берут время: без них хватает и пяти секунд, с ними — нет.
    _ = done.wait(timeout: .now() + (wantShots ? 40 : 20))
    exit(0)

case "calendar-status":
    print(calendarStatusJSON())

case "calendar-request":
    let asked = DispatchSemaphore(value: 0)
    askCalendar { _, _ in asked.signal() }
    _ = asked.wait(timeout: .now() + 120)
    print(calendarStatusJSON())

case "calendars":
    guard calendarGranted() else { print("[]"); exit(0) }
    print(calendarsJSON())

case "calendar":
    guard calendarGranted() else { print("[]"); exit(0) }
    print(eventsJSON(days: args.count > 2 ? (Int(args[2]) ?? 14) : 14))

case "calendar-add":
    guard args.count > 2 else {
        print("{\"ok\":false,\"error\":\"нет события\"}"); exit(0)
    }
    guard calendarWritable() else {
        print("{\"ok\":false,\"error\":\"нет доступа к календарю\"}"); exit(0)
    }
    print(addEvent(args[2]))

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

    // Команды с обычного ввода: картинку включают и выключают посреди созвона,
    // а перезапускать помощника ради этого нельзя — вместе с ним оборвётся
    // звук, которого второй раз уже не будет.
    //   video-on <bundle-id или -> <путь к mp4>
    //   video-off
    // Ответ уходит в тот же поток сообщений: «video-ok» или «video-fail …».
    DispatchQueue.global().async {
        while let line = readLine(strippingNewline: true) {
            let text = line.trimmingCharacters(in: .whitespaces)
            if text.isEmpty { continue }
            if text == "video-off" {
                Task {
                    let why = await capture.stopVideo()
                    note(why.isEmpty ? "video-ok" : "video-fail \(why)")
                }
                continue
            }
            if text.hasPrefix("video-on ") {
                // Путь может быть с пробелами, а bundle-id — нет: поэтому
                // сначала имя приложения, а остаток строки целиком путь.
                let rest = String(text.dropFirst("video-on ".count))
                guard let space = rest.firstIndex(of: " ") else {
                    note("video-fail нет пути"); continue
                }
                let app = String(rest[rest.startIndex..<space])
                let path = String(rest[rest.index(after: space)...])
                    .trimmingCharacters(in: .whitespaces)
                guard #available(macOS 14.0, *) else {
                    note("video-fail нужна macOS 14 или новее"); continue
                }
                Task {
                    let why = await capture.startVideo(path: path,
                                                       app: app == "-" ? nil : app)
                    note(why.isEmpty ? "video-ok" : "video-fail \(why)")
                }
                continue
            }
            note("неизвестная команда: \(text)")
        }
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
