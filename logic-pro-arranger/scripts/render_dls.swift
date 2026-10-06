// 用 macOS 自带的 GM 音色库（gs_instruments.dls）把音符事件离线渲染成 WAV。由 preview.py 调用，不用单独跑。
// 用法：swift render_dls.swift events.json out.wav
// events.json：{"sr": 44100, "tail": 2.0, "tracks": [{"program": 0, "drum": false, "volume": 0.8, "pan": 0.0,
//               "notes": [{"t": 秒, "d": 秒, "p": MIDI 号, "v": 力度}]}]}
import Foundation
import AVFoundation

struct Note: Decodable { let t: Double; let d: Double; let p: Int; let v: Int }
struct Trk: Decodable { let program: Int; let drum: Bool; let volume: Double; let pan: Double; let notes: [Note] }
struct Doc: Decodable { let sr: Double; let tail: Double; let tracks: [Trk] }

let args = CommandLine.arguments
guard args.count >= 3 else { print("用法：swift render_dls.swift events.json out.wav"); exit(2) }
let doc = try JSONDecoder().decode(Doc.self, from: Data(contentsOf: URL(fileURLWithPath: args[1])))
let dls = URL(fileURLWithPath: "/System/Library/Components/CoreAudio.component/Contents/Resources/gs_instruments.dls")
guard FileManager.default.fileExists(atPath: dls.path) else { print("没有系统 GM 音色库：\(dls.path)"); exit(3) }

let engine = AVAudioEngine()
let fmt = AVAudioFormat(standardFormatWithSampleRate: doc.sr, channels: 2)!
try engine.enableManualRenderingMode(.offline, format: fmt, maximumFrameCount: 512)
var samplers: [AVAudioUnitSampler] = []
for tr in doc.tracks {
    let s = AVAudioUnitSampler()
    engine.attach(s)
    engine.connect(s, to: engine.mainMixerNode, format: nil)
    let msb = tr.drum ? UInt8(0x78) : UInt8(0x79)          // GM 打击乐 / 旋律乐器音色库
    try s.loadSoundBankInstrument(at: dls, program: UInt8(tr.drum ? 0 : max(0, min(127, tr.program))), bankMSB: msb, bankLSB: 0)
    s.volume = Float(max(0, min(1, tr.volume)))
    s.pan = Float(max(-1, min(1, tr.pan)))
    samplers.append(s)
}
engine.mainMixerNode.outputVolume = 0.8
try engine.start()

struct Ev { let f: Int64; let on: Bool; let s: Int; let p: UInt8; let v: UInt8 }
var evs: [Ev] = []
var end: Double = 0
for (i, tr) in doc.tracks.enumerated() {
    for n in tr.notes where n.d > 0 && n.p >= 0 && n.p <= 127 {
        evs.append(Ev(f: Int64(n.t * doc.sr), on: true, s: i, p: UInt8(n.p), v: UInt8(max(1, min(127, n.v)))))
        evs.append(Ev(f: Int64((n.t + n.d) * doc.sr), on: false, s: i, p: UInt8(n.p), v: 0))
        end = max(end, n.t + n.d)
    }
}
evs.sort { $0.f == $1.f ? (!$0.on && $1.on) : $0.f < $1.f }
let total = Int64((end + doc.tail) * doc.sr)
let out = try AVAudioFile(forWriting: URL(fileURLWithPath: args[2]), settings: fmt.settings)
let buf = AVAudioPCMBuffer(pcmFormat: engine.manualRenderingFormat, frameCapacity: engine.manualRenderingMaximumFrameCount)!
var pos: Int64 = 0
var k = 0
while pos < total {
    while k < evs.count && evs[k].f <= pos {
        let e = evs[k]
        if e.on { samplers[e.s].startNote(e.p, withVelocity: e.v, onChannel: 0) }
        else { samplers[e.s].stopNote(e.p, onChannel: 0) }
        k += 1
    }
    var n = AVAudioFrameCount(min(Int64(256), total - pos))
    if k < evs.count { n = AVAudioFrameCount(max(1, min(Int64(n), evs[k].f - pos))) }
    let st = try engine.renderOffline(n, to: buf)
    if st != .success { print("渲染出错：\(st.rawValue)"); exit(4) }
    try out.write(from: buf)
    pos += Int64(n)
}
print(String(format: "ok %.1f s", Double(total) / doc.sr))
