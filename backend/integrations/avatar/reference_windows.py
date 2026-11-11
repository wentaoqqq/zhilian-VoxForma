"""Bound the automatic recognizer's input while sampling the whole recording."""
from pathlib import Path
import subprocess


def prepare_reference_windows(source, duration, destination, ffmpeg='ffmpeg'):
    source, destination = Path(source), Path(destination)
    count, seconds = 4, 15.0
    starts = [(duration-seconds)*i/(count-1) for i in range(count)]
    graph = f'[0:a]asplit={count}' + ''.join(f'[a{i}]' for i in range(count)) + ';'
    graph += ';'.join(f'[a{i}]atrim=start={start:.6f}:duration={seconds},asetpts=PTS-STARTPTS[s{i}]' for i,start in enumerate(starts))
    graph += ';' + ''.join(f'[s{i}]' for i in range(count)) + f'concat=n={count}:v=0:a=1[out]'
    temporary = destination.with_suffix('.wav.part')
    try:
        subprocess.run([str(ffmpeg),'-v','error','-y','-i',str(source),'-filter_complex',graph,
                        '-map','[out]','-ac','1','-ar','24000','-c:a','pcm_s16le','-f','wav',str(temporary)],
                       check=True,capture_output=True,timeout=180)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
