#!/usr/bin/env python3
"""
midi.py  —  Convert any MIDI file to a Roblox ZAP beat-sequencer Lua script.

Usage:
    python midi_to_roblox.py <input.mid> [options]

First, inspect your MIDI to see what tracks/channels it has:
    python midi_to_roblox.py song.mid --list-tracks

Then convert:
    python midi_to_roblox.py song.mid                          # auto-detect everything
    python midi_to_roblox.py song.mid --no-drums               # no drum groove
    python midi_to_roblox.py song.mid --melody-track 1 --bass-track 2
    python midi_to_roblox.py song.mid --melody-channel 3 --bass-channel 1   # Type 0 MIDIs
    python midi_to_roblox.py song.mid --merge-tracks           # smash all parts into melody
    python midi_to_roblox.py song.mid --bars 16 --bpm 120
    python midi_to_roblox.py song.mid --melody-sample 178      # 178 = Bell Bright

Options:
    --out <file>              Output .lua path (default: <input>_roblox.lua)
    --name <str>              Beat name in payload (default: filename stem)
    --bpm <int>               Override BPM (default: auto from MIDI)
    --bars <int>              Bars to export (default: all)
    --melody-track <int>      Track index for melody -- Type 1/2 MIDIs
    --bass-track <int>        Track index for bass   -- Type 1/2 MIDIs
    --melody-channel <int>    Channel (0-15) for melody -- Type 0 MIDIs
    --bass-channel <int>      Channel (0-15) for bass   -- Type 0 MIDIs
    --merge-tracks            Combine all non-drum parts into one melody layer
    --drums / --no-drums      Add kick+snare+hihat groove (default: on)
    --melody-sample <int>     Roblox sound ID for melody (default: 61 = Piano-C)
    --bass-sample <int>       Roblox sound ID for bass   (default: 1  = Bass Synth)
    --transpose <int>         Semitone shift for melody (default: 0)
    --bass-transpose <int>    Semitone shift for bass   (default: 0)
    --quantize 8|16           Grid cells per bar (default: 16)
    --list-tracks             Print track/channel info and exit
    --verbose                 Show per-bar detail during conversion
"""

import argparse
import os
import sys

try:
    import mido
except ImportError:
    sys.exit("mido not installed. Run:  pip install mido")

SOUND_PIANO_C        = 61
SOUND_BASS_SYNTH     = 1
SOUND_KICK_BASIC     = 17
SOUND_SNARE_CLEAN    = 4
SOUND_HIHAT_METALLIC = 15
SOUND_BELL_BRIGHT    = 178

NOTE_NAMES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']

def note_name(n):
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"

def get_tempo(mid):
    for track in mid.tracks:
        cum = 0
        for msg in track:
            cum += msg.time
            if msg.type == 'set_tempo':
                return round(mido.tempo2bpm(msg.tempo))
    return 120


def collect_notes_from_track(track):
    notes, cum = [], 0
    for msg in track:
        cum += msg.time
        if msg.type == 'note_on' and msg.velocity > 0:
            notes.append((cum, msg.note, msg.velocity))
    return notes


def collect_notes_from_channel(tracks, channel):
    notes = []
    for track in tracks:
        cum = 0
        for msg in track:
            cum += msg.time
            if msg.type == 'note_on' and msg.velocity > 0 and msg.channel == channel:
                notes.append((cum, msg.note, msg.velocity))
    notes.sort(key=lambda x: x[0])
    return notes


def is_drum_track(track):
    name = (track.name or '').lower()
    if any(k in name for k in ('drum', 'perc', 'kit')):
        return True
    for msg in track:
        if hasattr(msg, 'channel') and msg.channel == 9:
            return True
    return False


def channel_summary(mid):
    by_ch = {}
    progs = {}
    for track in mid.tracks:
        cum = 0
        for msg in track:
            cum += msg.time
            if msg.type == 'program_change':
                progs[msg.channel] = msg.program
            if msg.type == 'note_on' and msg.velocity > 0:
                by_ch.setdefault(msg.channel, []).append((cum, msg.note))
    result = {}
    for ch, notes in by_ch.items():
        pitches = [n for _, n in notes]
        result[ch] = {
            'notes':     len(notes),
            'avg_pitch': sum(pitches) / len(pitches),
            'min':       min(pitches),
            'max':       max(pitches),
            'program':   progs.get(ch),
            'is_drum':   ch == 9,
        }
    return result


def auto_pick_tracks(mid):
    candidates = []
    for i, track in enumerate(mid.tracks):
        if is_drum_track(track):
            continue
        notes = collect_notes_from_track(track)
        if not notes:
            continue
        pitches = [n for _, n, _ in notes]
        candidates.append((i, sum(pitches) / len(pitches), len(notes)))
    if not candidates:
        sys.exit(
            "No non-drum tracks with notes found.\n"
            "Try --list-tracks to inspect, or --melody-channel N for Type 0 MIDIs."
        )
    candidates.sort(key=lambda x: -x[1])
    mel = candidates[0][0]
    bas = candidates[1][0] if len(candidates) > 1 else None
    return mel, bas


def auto_pick_channels(mid):
    summary = channel_summary(mid)
    non_drum = [(ch, info) for ch, info in summary.items()
                if not info['is_drum'] and info['notes'] > 0]
    if not non_drum:
        sys.exit("No non-drum channels found. Try --list-tracks.")
    non_drum.sort(key=lambda x: -x[1]['avg_pitch'])
    mel = non_drum[0][0]
    bas = non_drum[1][0] if len(non_drum) > 1 else None
    return mel, bas

def quantize(notes, tpb, cells=16, max_bars=None, transpose=0):
    ticks_per_bar  = tpb * 4
    ticks_per_cell = ticks_per_bar // cells
    bars = []
    for abs_tick, midi_note, _ in notes:
        bar_idx  = abs_tick // ticks_per_bar
        pos_tick = abs_tick % ticks_per_bar
        cell     = round(pos_tick / ticks_per_cell)
        cell     = min(cell, cells - 1)
        if max_bars is not None and bar_idx >= max_bars:
            continue
        while len(bars) <= bar_idx:
            bars.append({})
        pitch = max(-127, min(127, midi_note + transpose - 60))
        bars[bar_idx].setdefault(pitch, [])
        cell_1 = cell + 1
        if cell_1 not in bars[bar_idx][pitch]:
            bars[bar_idx][pitch].append(cell_1)
    if max_bars:
        while len(bars) < max_bars:
            bars.append({})
    return bars


def pad_bars(bars, target):
    while len(bars) < target:
        bars.append({})
    return bars

def calc_buffer_size(name, num_bars, melody_bars, bass_bars, drums):
    bytes_per_track = 37
    drum_tracks = 3 if drums else 0
    total = 3 + 2 + len(name) + 2 + 2  # fixed header
    for i in range(num_bars):
        n_mel  = len(melody_bars[i]) if i < len(melody_bars) else 0
        n_bas  = len(bass_bars[i])   if i < len(bass_bars)   else 0
        n_trks = drum_tracks + n_mel + n_bas
        if n_trks == 0:
            n_trks = 1
        total += 2 + n_trks * bytes_per_track + 2 + 1
    needed = int(total * 1.25) + 128
    size = 1
    while size < needed:
        size <<= 1
    return size

def bars_to_lua(bars, varname):
    lines = [f"local {varname} = {{"]
    for i, bar in enumerate(bars):
        parts = []
        for pitch in sorted(bar.keys()):
            cells = sorted(set(bar[pitch]))
            parts.append(f"{{pitch={pitch},cells={{{','.join(str(c) for c in cells)}}}}}")
        lines.append(f"    -- Bar {i+1}")
        if parts:
            lines.append(f"    {{ {', '.join(parts)} }},")
        else:
            lines.append("    {},  -- rest bar")
    lines.append("}")
    return '\n'.join(lines)

LUA_TEMPLATE = '''\
-- Auto-generated by midi.py
-- Made by Fxke
-- Source : {source_file}
-- BPM    : {bpm}  |  Bars: {num_bars}  |  Grid: {cells} cells/bar

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local MELODY_SAMPLE  = {melody_sample}   -- 61=Piano-C  178=Bell Bright
local BASS_SAMPLE    = {bass_sample}     -- 1=Bass Synth
local KICK_BASIC     = 17
local SNARE_CLEAN    = 4
local HIHAT_METALLIC = 15

{melody_table}

{bass_table}

local function buildBeat()
    local buf = buffer.create({buf_size})
    local pos = 0

    local function writeU8(v)  buffer.writeu8(buf,  pos, v); pos = pos + 1 end
    local function writeI8(v)  buffer.writei8(buf,  pos, v); pos = pos + 1 end
    local function writeU16(v) buffer.writeu16(buf, pos, v); pos = pos + 2 end
    local function writeI16(v) buffer.writei16(buf, pos, v); pos = pos + 2 end
    local function writeStr(s)
        writeU16(#s)
        buffer.writestring(buf, pos, s, #s)
        pos = pos + #s
    end

    local BPM      = {bpm}
    local CELLS    = {cells}
    local NUM_BARS = {num_bars}

    writeU8(18); writeU8(1); writeU8(0)
    writeStr("{beat_name}")
    writeI16(BPM)
    writeU16(NUM_BARS)

    local function makeSeq(soundId, cells_list)
        local seq = {{}}
        for i = 1, CELLS do seq[i] = 0 end
        for _, c in ipairs(cells_list) do seq[c] = soundId end
        return seq
    end

    local function writeTrack(notes, pitch, volume)
        volume = volume or 100
        pitch  = pitch  or 0
        writeU8(0x03)
        writeU16(#notes)
        for _, n in ipairs(notes) do writeI16(n) end
        writeI8(volume)
        writeI8(pitch)
    end

    for barIdx = 1, NUM_BARS do
        local lead = melodyBars[barIdx] or {{}}
        local bass = bassBars[barIdx]   or {{}}

        local drumCount  = {drum_count}
        local trackCount = drumCount + #lead + #bass
        if trackCount == 0 then trackCount = 1 end

        writeU16(trackCount)

        if drumCount >= 3 then
            writeTrack(makeSeq(KICK_BASIC,     {{1,5,9,13}}),  0, 100)
            writeTrack(makeSeq(SNARE_CLEAN,    {{5,13}}),       0, 100)
            writeTrack(makeSeq(HIHAT_METALLIC, {{3,7,11,15}}), 0,  60)
        end

        for _, t in ipairs(lead) do
            writeTrack(makeSeq(MELODY_SAMPLE, t.cells), t.pitch, 100)
        end

        for _, t in ipairs(bass) do
            writeTrack(makeSeq(BASS_SAMPLE, t.cells), t.pitch, 100)
        end

        if trackCount == 1 and drumCount == 0 and #lead == 0 and #bass == 0 then
            writeTrack(makeSeq(0, {{}}))
        end

        writeI16(BPM)
        writeI8(CELLS)
    end

    local final = buffer.create(pos)
    buffer.copy(final, 0, buf, 0, pos)
    return final
end

local payload = buildBeat()
local remote  = ReplicatedStorage:WaitForChild("ZAP"):WaitForChild("ZAP_RELIABLE")
remote:FireServer(payload, {{}})

print("[{beat_name}] Sent -- {num_bars} bars @ {bpm} BPM.")
'''

def print_track_list(mid):
    bpm = get_tempo(mid)
    fname = getattr(mid, 'filename', args.input if 'args' in dir() else '?')
    print(f"\nFile : {fname}")
    print(f"Type : {mid.type}  |  TPB: {mid.ticks_per_beat}  |  BPM: {bpm}\n")

    if mid.type == 0:
        print("Type 0 MIDI -- everything on one track, split by MIDI channel.")
        print("Use --melody-channel N and --bass-channel N when converting.\n")
        summary = channel_summary(mid)
        print(f"  {'Ch':>3}  {'Drum':>5}  {'Prog':>5}  {'Notes':>6}  {'AvgPitch':>9}  Range")
        print(f"  {'-'*3}  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*9}  {'-'*14}")
        for ch in sorted(summary):
            info = summary[ch]
            rng  = f"{note_name(info['min'])}-{note_name(info['max'])}"
            drum = 'YES' if info['is_drum'] else 'no'
            print(f"  {ch:>3}  {drum:>5}  {str(info['program']):>5}  "
                  f"{info['notes']:>6}  {info['avg_pitch']:>9.1f}  {rng}")
    else:
        print("Type 1/2 MIDI -- separate tracks.")
        print("Use --melody-track N and --bass-track N when converting.\n")
        print(f"  {'Idx':>3}  {'Name':<24}  {'Notes':>6}  {'Drum':>5}  {'AvgPitch':>9}  Range")
        print(f"  {'-'*3}  {'-'*24}  {'-'*6}  {'-'*5}  {'-'*9}  {'-'*14}")
        for i, track in enumerate(mid.tracks):
            notes = collect_notes_from_track(track)
            drum  = is_drum_track(track)
            if notes:
                pitches = [n for _, n, _ in notes]
                avg_p   = sum(pitches) / len(pitches)
                rng     = f"{note_name(min(pitches))}-{note_name(max(pitches))}"
            else:
                avg_p, rng = 0.0, '--'
            print(f"  {i:>3}  {(track.name or ''):24s}  {len(notes):>6}  "
                  f"{'YES' if drum else 'no':>5}  {avg_p:>9.1f}  {rng}")
    print()

def pick_best_melody_track(mid):
    best = None
    best_score = 0

    for i, track in enumerate(mid.tracks):
        if is_drum_track(track):
            continue

        notes = collect_notes_from_track(track)
        if len(notes) < 30:
            continue

        pitches = [n for _, n, _ in notes]
        span = max(pitches) - min(pitches)
        score = len(notes) * 2 - span

        if score > best_score:
            best_score = score
            best = i

    return best

def convert(args):
    mid = mido.MidiFile(args.input)
    tpb = mid.ticks_per_beat

    if args.list_tracks:
        print_track_list(mid)
        return

    bpm       = args.bpm or get_tempo(mid)
    beat_name = (args.name or os.path.splitext(os.path.basename(args.input))[0])[:32]

    if args.verbose:
        print(f"\nSource: {args.input}  |  BPM: {bpm}  |  TPB: {tpb}  |  Type: {mid.type}")

    if args.merge_tracks:
        melody_notes = []
        for i, track in enumerate(mid.tracks):
            if not is_drum_track(track):
                melody_notes.extend(collect_notes_from_track(track))
        melody_notes.sort(key=lambda x: x[0])
        bass_notes = []
        if args.verbose:
            print(f"Merge mode: {len(melody_notes)} notes from all non-drum parts")

    elif mid.type == 0 or args.melody_channel is not None:
        if args.melody_channel is not None:
            mel_ch = args.melody_channel
            bas_ch = args.bass_channel
        else:
            mel_ch, bas_ch = auto_pick_channels(mid)

        melody_notes = collect_notes_from_channel(mid.tracks, mel_ch)
        bass_notes   = collect_notes_from_channel(mid.tracks, bas_ch) if bas_ch is not None else []

        if args.verbose:
            print(f"Channel mode: melody=ch{mel_ch}  bass={'ch'+str(bas_ch) if bas_ch is not None else 'none'}")

    else:
        if args.melody_track is not None:
            mel_idx = args.melody_track
            bas_idx = args.bass_track
        else:
            mel_idx = pick_best_melody_track(mid)
            bas_idx = None
            if args.bass_track is not None:
                bas_idx = args.bass_track

        melody_notes = collect_notes_from_track(mid.tracks[mel_idx])
        bass_notes   = collect_notes_from_track(mid.tracks[bas_idx]) if bas_idx is not None else []

        if args.verbose:
            print(f"Track mode: melody=track{mel_idx}  bass={'track'+str(bas_idx) if bas_idx is not None else 'none'}")

    if not melody_notes:
        sys.exit("No melody notes found. Run --list-tracks and pick your channels/tracks manually.")

    ticks_per_bar = tpb * 4
    all_ticks = [t for t, _, _ in melody_notes + bass_notes]
    auto_bars = (max(all_ticks) // ticks_per_bar) + 1
    num_bars  = args.bars or auto_bars

    if args.verbose:
        print(f"Bars: {num_bars}  |  Melody notes: {len(melody_notes)}  |  Bass notes: {len(bass_notes)}")

    melody_bars = pad_bars(quantize(melody_notes, tpb, args.quantize, num_bars, args.transpose),      num_bars)
    bass_bars   = pad_bars(quantize(bass_notes,   tpb, args.quantize, num_bars, args.bass_transpose), num_bars)

    if args.verbose:
        print("\nMelody preview (first 4 bars):")
        for i, bar in enumerate(melody_bars[:4]):
            print(f"  Bar {i+1}: { {p: sorted(cs) for p, cs in bar.items()} }")

    drum_count = 3 if args.drums else 0
    buf_size   = calc_buffer_size(beat_name, num_bars, melody_bars, bass_bars, args.drums)
    melody_tbl = bars_to_lua(melody_bars, "melodyBars")
    bass_tbl   = bars_to_lua(bass_bars,   "bassBars")

    lua = LUA_TEMPLATE.format(
        source_file   = os.path.basename(args.input),
        bpm           = bpm,
        num_bars      = num_bars,
        cells         = args.quantize,
        beat_name     = beat_name,
        melody_sample = args.melody_sample,
        bass_sample   = args.bass_sample,
        buf_size      = buf_size,
        drum_count    = drum_count,
        melody_table  = melody_tbl,
        bass_table    = bass_tbl,
    )

    out_path = args.out or (os.path.splitext(args.input)[0] + '_roblox.lua')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(lua)

    mel_events = sum(len(cs) for bar in melody_bars for cs in bar.values())
    bas_events = sum(len(cs) for bar in bass_bars   for cs in bar.values())
    print(f"\n  Generated : {out_path}")
    print(f"  Name      : {beat_name}")
    print(f"  BPM       : {bpm}")
    print(f"  Bars      : {num_bars}")
    print(f"  Grid      : {args.quantize} cells/bar")
    print(f"  Drums     : {'kick + snare + hihat' if args.drums else 'none'}")
    print(f"  Melody    : {mel_events} note events")
    print(f"  Bass      : {bas_events} note events")
    print(f"  Buffer    : {buf_size} bytes (exact needed: ~{int(buf_size/1.25)})")

def main():
    p = argparse.ArgumentParser(
        description='Convert any MIDI to a Roblox ZAP beat-sequencer Lua script.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('input', help='Input .mid file')
    p.add_argument('--out',  help='Output .lua path')
    p.add_argument('--name', help='Beat name (default: filename stem)')
    p.add_argument('--bpm',  type=int, help='Override BPM')
    p.add_argument('--bars', type=int, help='Number of bars to export')

    p.add_argument('--melody-track',   type=int, dest='melody_track')
    p.add_argument('--bass-track',     type=int, dest='bass_track')
    p.add_argument('--melody-channel', type=int, dest='melody_channel',
                   help='MIDI channel for melody (0-15) -- required for Type 0 MIDIs')
    p.add_argument('--bass-channel',   type=int, dest='bass_channel',
                   help='MIDI channel for bass   (0-15)')
    p.add_argument('--merge-tracks',   action='store_true', dest='merge_tracks')

    p.add_argument('--drums',    action='store_true',  default=True,  dest='drums')
    p.add_argument('--no-drums', action='store_false',                dest='drums')

    p.add_argument('--melody-sample', type=int, default=SOUND_PIANO_C,    dest='melody_sample')
    p.add_argument('--bass-sample',   type=int, default=SOUND_BASS_SYNTH, dest='bass_sample')

    p.add_argument('--transpose',      type=int, default=0)
    p.add_argument('--bass-transpose', type=int, default=0, dest='bass_transpose')
    p.add_argument('--quantize',       type=int, default=16, choices=[8, 16])

    p.add_argument('--list-tracks', action='store_true', dest='list_tracks')
    p.add_argument('--verbose',     action='store_true')

    args = p.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"File not found: {args.input}")

    convert(args)


if __name__ == '__main__':
    main()
