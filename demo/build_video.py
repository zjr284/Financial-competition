"""将实际截图、逐句中文音轨与已审阅讲稿组合为带字幕的视频草稿。"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import wave

from PIL import Image, ImageDraw, ImageFont, ImageOps


def wrap(text, draw, font, width):
    lines, current = [], ''
    for char in text:
        if current and draw.textlength(current + char, font=font) > width:
            lines.append(current)
            current = ''
        current += char
    return lines + ([current] if current else [])


def timing(scene, lengths):
    duration = Decimal(str(scene['duration']))
    if duration <= 1 or not lengths or any(n <= 0 for n in lengths):
        raise ValueError('片段与音轨时长须大于零')
    speed = max(Decimal(1), sum(lengths) / (duration - Decimal(1)))
    if speed > Decimal('1.35'):
        raise ValueError('配音过长，请删减讲稿，不要过度加速')
    gap = (duration - sum(lengths) / speed) / len(lengths)
    return speed, [n / speed + gap for n in lengths]


def build(storyboard, assets, voices, output, font_path):
    storyboard, assets, voices, output = map(Path, (storyboard, assets, voices, output))
    if output.exists():
        raise ValueError('使用全新目录，保留旧剪辑')
    plan = json.loads(storyboard.read_text(encoding='utf-8'))
    scenes = plan['scenes']
    total = sum(Decimal(str(s['duration'])) for s in scenes)
    if total > 300:
        raise ValueError('讲稿时长超过 5 分钟')
    inputs = [storyboard]
    prepared = []
    for i, scene in enumerate(scenes):
        lengths, audio = [], []
        for j, text in enumerate(scene['speech']):
            if not text.strip():
                raise ValueError('字幕不可为空')
            file = voices / f'{i:02}-{j:02}.wav'
            with wave.open(str(file)) as wav:
                lengths.append(Decimal(wav.getnframes()) / wav.getframerate())
            audio.append(file)
        speed, durations = timing(scene, lengths)
        names = scene.get('assets', [scene['asset']] * len(audio))
        if len(names) != len(audio):
            raise ValueError('逐句素材数量与配音不一致')
        pictures = [assets / name if name else None for name in names]
        for picture in pictures:
            if picture and not picture.is_file():
                raise ValueError(f'缺少实际素材：{picture.name}')
        inputs += audio + [p for p in pictures if p]
        prepared.append((scene, audio, pictures, speed, durations))
    output.mkdir(parents=True)
    fonts = {size: ImageFont.truetype(str(font_path), size) for size in (24, 28, 32, 38, 48, 62)}
    subtitles, cursor, concat = [], Decimal(0), []
    def stamp(t):
        millis = int(t * 1000)
        return f'{millis//3600000:02}:{millis//60000%60:02}:{millis//1000%60:02},{millis%1000:03}'
    for i, (scene, audio, pictures, speed, durations) in enumerate(prepared):
        for j, (speech, wav, asset, duration) in enumerate(zip(scene['speech'], audio, pictures, durations)):
            image = Image.new('RGB', (1920,1080), '#f2f6f7')
            draw = ImageDraw.Draw(image)
            draw.rectangle((0,0,1920,170), fill='#102f3b')
            draw.text((60,34), f'{i+1:02} / 09   {scene["title"]}', font=fonts[48], fill='white')
            draw.text((60,108), scene['label'], font=fonts[28], fill='#9bdec8')
            if asset:
                with Image.open(asset) as source:
                    screenshot = ImageOps.contain(source.convert('RGB'), (1370,720))
                    image.paste(screenshot, (520+(1370-screenshot.width)//2,185))
                y=225
                for bullet in scene['bullets']:
                    for line in wrap(bullet,draw,fonts[32],400):
                        draw.text((60,y),line,font=fonts[32],fill='#163f4c');y+=48
                    y+=44
            else:
                y=240
                for n, bullet in enumerate(scene['bullets'],1):
                    draw.rounded_rectangle((80,y,1840,y+142),radius=18,fill='white')
                    draw.text((115,y+32),f'0{n}',font=fonts[62],fill='#087e80')
                    draw.text((245,y+44),bullet,font=fonts[38],fill='#163f4c');y+=187
            draw.rectangle((0,915,1920,1080),fill='#102f3b')
            lines=wrap(speech,draw,fonts[38],1790)
            if len(lines)>2:
                raise ValueError('单条字幕过长，请拆句')
            for n,line in enumerate(lines):
                draw.text(((1920-draw.textlength(line,font=fonts[38]))/2,940+n*48),line,font=fonts[38],fill='white')
            draw.text((60,1040),plan['notice'],font=fonts[24],fill='#b3c6cd')
            frame=output/f'{i:02}-{j:02}.png';image.save(frame)
            clip=output/f'{i:02}-{j:02}.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-n','-loop','1','-framerate','25','-i',str(frame),
                '-i',str(wav),'-af',f'atempo={speed},apad','-t',str(duration),'-r','25','-c:v','libx264',
                '-preset','fast','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2',str(clip)],check=True)
            concat.append(f"file '{clip.name}'")
            subtitles.append(f'{len(subtitles)+1}\n{stamp(cursor)} --> {stamp(cursor+duration)}\n{speech}\n')
            cursor += duration
    (output/'clips.txt').write_text('\n'.join(concat),encoding='utf-8')
    (output/'字幕.srt').write_text('\n'.join(subtitles),encoding='utf-8-sig')
    final=output/'金融投研智能体_初赛审阅稿.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-n','-f','concat','-safe','0','-i',str(output/'clips.txt'),
                    '-c','copy','-movflags','+faststart',str(final)],check=True)
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries',
        'format=duration,size:stream=codec_type,codec_name,width,height','-of','json',str(final)],text=True))
    if Decimal(probe['format']['duration'])>300 or int(probe['format']['size'])>500_000_000:
        raise ValueError('导出超过时长或大小限制')
    if not any(s['codec_type']=='audio' for s in probe['streams']):
        raise ValueError('导出缺少音轨')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    report={'code_version':revision,'duration_planned':str(total),'probe':probe,'review_draft':True,
            'human_gold':False,'holdout_metrics':None,
            'input_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
            'video_sha256':hashlib.sha256(final.read_bytes()).hexdigest()}
    (output/'build-proof.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for i in (0,3,4,5,6,7,8):
        shutil.copyfile(output/f'{i:02}-00.png',output/f'chapter-{i+1:02}.png')
    print(json.dumps({'output':str(final),'duration':probe['format']['duration'],'bytes':probe['format']['size']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('storyboard','assets','voices','output','font'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    build(args.storyboard,args.assets,args.voices,args.output,args.font)
