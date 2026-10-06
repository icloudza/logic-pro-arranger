// Public Apple frameworks only. File input; no microphone or cloud recognition.
import Foundation
import AVFoundation
import SoundAnalysis
import Speech

struct ToolError: Error { let reason: String }
func output(_ object: [String: Any], code: Int32 = 0) -> Never {
    do {
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10]))
    } catch { fputs("{\"error\":\"JSON serialization failed\"}\n", stderr) }
    exit(code)
}

@available(macOS 12.0, *)
final class SoundObserver: NSObject, SNResultsObserving {
    var events: [[String: Any]] = []
    var error: Error?
    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let result = result as? SNClassificationResult else { return }
        events.append(["t0": result.timeRange.start.seconds, "t1": CMTimeRangeGetEnd(result.timeRange).seconds,
            "candidates": result.classifications.prefix(5).map { ["label": $0.identifier, "score": $0.confidence] as [String: Any] }])
    }
    func request(_ request: SNRequest, didFailWithError error: Error) { self.error = error }
    func requestDidComplete(_ request: SNRequest) {}
}

@available(macOS 12.0, *)
func soundFile(_ path: String) throws -> [String: Any] {
    let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
    let duration = Double(file.length) / file.processingFormat.sampleRate
    let desired = min(3.0, duration)
    switch request.windowDurationConstraint {
    case .enumeratedDurations(let times):
        if let closest = times.min(by: { abs($0.seconds-desired) < abs($1.seconds-desired) }) { request.windowDuration = closest }
    case .durationRange(let range):
        request.windowDuration = CMTime(seconds: max(range.start.seconds, min(CMTimeRangeGetEnd(range).seconds, desired)), preferredTimescale: 48000)
    @unknown default: break
    }
    request.overlapFactor = 0.5
    let observer = SoundObserver()
    let analyzer = try SNAudioFileAnalyzer(url: URL(fileURLWithPath: path))
    try analyzer.add(request, withObserver: observer)
    analyzer.analyze()
    if let error = observer.error { throw error }
    return ["backend": "apple-soundanalysis", "classifier": "version1",
            "window_s": request.windowDuration.seconds, "events": observer.events,
            "label_count": request.knownClassifications.count,
            "os": ProcessInfo.processInfo.operatingSystemVersionString]
}

#if compiler(>=6.2)
@available(macOS 26.0, *)
func modernStatus(_ localeID: String) async -> [String: Any] {
    guard SpeechTranscriber.isAvailable else { return ["available": false, "reason": "device_not_supported"] }
    guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: localeID)) else {
        return ["available": false, "reason": "locale_not_supported", "locale": localeID]
    }
    let transcriber = SpeechTranscriber(locale: locale, preset: .timeIndexedTranscriptionWithAlternatives)
    let state = await AssetInventory.status(forModules: [transcriber])
    return ["available": true, "locale": locale.identifier, "assets": String(describing: state),
            "installed_locales": await SpeechTranscriber.installedLocales.map(\.identifier), "on_device": true]
}

@available(macOS 26.0, *)
func modernSpeech(_ path: String, localeID: String, install: Bool) async throws -> [String: Any] {
    guard SpeechTranscriber.isAvailable,
          let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: localeID)) else {
        throw ToolError(reason: "SpeechTranscriber 不支持该设备或语言")
    }
    let transcriber = SpeechTranscriber(locale: locale, preset: .timeIndexedTranscriptionWithAlternatives)
    let modules: [any SpeechModule] = [transcriber]
    if await AssetInventory.status(forModules: modules) != .installed {
        guard install else { throw ToolError(reason: "speech_assets_not_installed；可显式传 --install-assets 下载 Apple 本地语音模型") }
        if let request = try await AssetInventory.assetInstallationRequest(supporting: modules) {
            try await request.downloadAndInstall()
        }
    }
    let analyzer = SpeechAnalyzer(modules: modules)
    let collector = Task { () throws -> [SpeechTranscriber.Result] in
        var results: [SpeechTranscriber.Result] = []
        for try await result in transcriber.results { results.append(result) }
        return results
    }
    do {
        let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
        if let end = try await analyzer.analyzeSequence(from: file) { try await analyzer.finalizeAndFinish(through: end) }
        else { await analyzer.cancelAndFinishNow() }
        let results = try await collector.value
        let events: [[String: Any]] = results.map { r in
            let words: [[String: Any]] = r.text.runs.compactMap { run in
                guard let time = run.audioTimeRange else { return nil }
                return ["text": String(r.text[run.range].characters), "t0": time.start.seconds,
                        "t1": CMTimeRangeGetEnd(time).seconds]
            }
            return ["text": String(r.text.characters), "t0": r.range.start.seconds,
                    "t1": CMTimeRangeGetEnd(r.range).seconds, "words": words,
                    "alternatives": r.alternatives.prefix(3).map { String($0.characters) }]
        }
        return ["backend": "apple-speechanalyzer", "locale": locale.identifier, "on_device": true,
                "events": events, "os": ProcessInfo.processInfo.operatingSystemVersionString]
    } catch {
        collector.cancel(); await analyzer.cancelAndFinishNow(); throw error
    }
}
#endif

@main
struct AppleAudio {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        let localeIndex = args.firstIndex(of: "--locale")
        let locale = localeIndex.flatMap { $0+1 < args.count ? args[$0+1] : nil } ?? "zh-CN"
        do {
            guard let command = args.first else { throw ToolError(reason: "需要 capabilities / labels / sounds FILE / speech FILE") }
            if command == "capabilities" {
                var result: [String: Any] = ["platform": "macOS", "os": ProcessInfo.processInfo.operatingSystemVersionString,
                    "coreml": "runtime_available_not_a_builtin_music_model", "soundanalysis": ["available": false]]
                if #available(macOS 12.0, *) {
                    let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
                    result["soundanalysis"] = ["available": true, "classifier": "version1",
                        "label_count": request.knownClassifications.count, "window_s": request.windowDuration.seconds]
                }
                result["legacy_speech_authorization"] = SFSpeechRecognizer.authorizationStatus().rawValue
                #if compiler(>=6.2)
                if #available(macOS 26.0, *) { result["speech"] = await modernStatus(locale) }
                else { result["speech"] = ["available": false, "reason": "SpeechAnalyzer requires macOS 26"] }
                #else
                result["speech"] = ["available": false, "reason": "SpeechAnalyzer requires newer SDK"]
                #endif
                output(result)
            }
            if command == "labels", #available(macOS 12.0, *) {
                output(["labels": try SNClassifySoundRequest(classifierIdentifier: .version1).knownClassifications])
            }
            guard args.count >= 2 else { throw ToolError(reason: "缺少文件路径") }
            if command == "sounds", #available(macOS 12.0, *) { output(try soundFile(args[1])) }
            #if compiler(>=6.2)
            if command == "speech", #available(macOS 26.0, *) {
                output(try await modernSpeech(args[1], localeID: locale, install: args.contains("--install-assets")))
            }
            #endif
            throw ToolError(reason: "当前系统不支持该 Apple 后端")
        } catch {
            let reason = (error as? ToolError)?.reason ?? error.localizedDescription
            output(["error": reason, "backend": "apple", "status": "unavailable"], code: 2)
        }
    }
}
