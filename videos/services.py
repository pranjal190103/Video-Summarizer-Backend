import json
import os
import subprocess
import tempfile
import shutil
from django.conf import settings
from openai import OpenAI

CATEGORIES = ['Design', 'Content', 'Marketing']

def transcribe_video(uploaded_file) -> str:
    """Extract and transcribe video audio in short chunks to stay under API limits."""
    if not settings.OPENAI_API_KEY:
        raise RuntimeError('Set OPENAI_API_KEY on the backend to transcribe uploaded videos.')
    if uploaded_file.size > settings.MAX_VIDEO_SIZE_BYTES:
        raise ValueError(f'Video is larger than the configured {settings.MAX_VIDEO_SIZE_GB} GB processing limit.')
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as error:
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            raise RuntimeError('Install backend requirements to enable video audio extraction (imageio-ffmpeg).') from error
    suffix = os.path.splitext(uploaded_file.name)[1] or '.mp4'
    with tempfile.TemporaryDirectory() as temp_dir:
        video_path = os.path.join(temp_dir, f'video{suffix}')
        audio_pattern = os.path.join(temp_dir, 'audio-%05d.mp3')
        with open(video_path, 'wb') as target:
            for chunk in uploaded_file.chunks():
                target.write(chunk)
        result = subprocess.run([
            ffmpeg, '-y', '-i', video_path, '-vn', '-ac', '1', '-ar', '16000',
            '-b:a', '48k', '-f', 'segment', '-segment_time', '480',
            '-reset_timestamps', '1', '-segment_format', 'mp3', audio_pattern,
        ], capture_output=True, text=True, timeout=7200)
        if result.returncode != 0:
            raise ValueError('Could not read an audio track from this video. Check the format and try another file.')
        audio_paths = sorted(
            os.path.join(temp_dir, name) for name in os.listdir(temp_dir)
            if name.startswith('audio-') and name.endswith('.mp3')
        )
        if not audio_paths:
            raise ValueError('No audio could be extracted from this video.')
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        transcript_parts = []
        for index, audio_path in enumerate(audio_paths, start=1):
            if os.path.getsize(audio_path) > 25 * 1024 * 1024:
                raise ValueError(f'Audio segment {index} exceeds OpenAI’s 25 MB transcription limit.')
            with open(audio_path, 'rb') as audio:
                transcription = client.audio.transcriptions.create(
                    model='whisper-1', file=audio, response_format='text',
                )
            segment_text = str(transcription).strip()
            if segment_text:
                transcript_parts.append(segment_text)
        text = '\n\n'.join(transcript_parts)
        if not text:
            raise ValueError('No speech was detected in this video.')
        return text

def analyze_transcript(transcript: str, title: str) -> dict:
    """Summarize a transcript with OpenAI and normalize its structured response."""
    if settings.OPENAI_API_KEY:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0,
            response_format={
                'type': 'json_schema',
                'json_schema': {
                    'name': 'video_analysis',
                    'strict': True,
                    'schema': {
                        'type': 'object',
                        'properties': {
                            'summary': {'type': 'string'},
                            'categories': {'type': 'array', 'items': {'type': 'string', 'enum': CATEGORIES}},
                            'keyTopics': {'type': 'array', 'items': {'type': 'string'}},
                            'kbScore': {'type': 'integer'},
                            'trainingReady': {'type': 'boolean'},
                        },
                        'required': ['summary', 'categories', 'keyTopics', 'kbScore', 'trainingReady'],
                        'additionalProperties': False,
                    },
                },
            },
            messages=[
                {'role': 'system', 'content': 'Analyze enterprise training video transcripts. Return a factual, concise 40-80 word summary paragraph, one or more categories, 3-8 short key topic phrases, an integer knowledge base usefulness score from 0 to 100, and a boolean trainingReady. Allowed categories: Design (UX/UI/product design/research/design systems), Content (writing/documentation/editorial/knowledge management), Marketing (campaigns/branding/SEO/social/advertising/acquisition). Do not infer unsupported claims. trainingReady requires substantive, clear learning material.'},
                {'role': 'user', 'content': f'Title: {title}\n\nTranscript:\n{transcript[:100000]}'},
            ],
        )
        choice = response.choices[0] if response.choices else None
        message = choice.message if choice else None
        content = message.content if message else None
        if not content:
            raise ValueError('OpenAI did not return a complete analysis. Try again or use a shorter transcript.')
        result = json.loads(content)

        # Normalize field names and values defensively for compatible API/model responses.
        normalized = {str(key).replace('_', '').casefold(): value for key, value in result.items()}
        summary = str(normalized.get('summary') or '').strip()
        raw_categories = normalized.get('categories')
        if not isinstance(raw_categories, list):
            raw_categories = []
        category_lookup = {category.casefold(): category for category in CATEGORIES}
        categories = list(dict.fromkeys(category_lookup[str(value).strip().casefold()]
                                        for value in raw_categories
                                        if str(value).strip().casefold() in category_lookup))
        raw_topics = normalized.get('keytopics')
        if not isinstance(raw_topics, list):
            raw_topics = []
        topics = list(dict.fromkeys(str(value).strip()[:100] for value in raw_topics if str(value).strip()))[:8]
        try:
            score = max(0, min(100, int(normalized.get('kbscore', 0))))
        except (TypeError, ValueError):
            score = 0
        training_ready = normalized.get('trainingready', False)
        if not summary:
            raise ValueError('OpenAI analysis did not include a summary. Try again.')
        if not categories:
            raise ValueError('OpenAI analysis did not include a supported category. Try again.')
        if not topics:
            raise ValueError('OpenAI analysis did not include key topics. Try again.')
        return {'summary': summary, 'categories': categories, 'keyTopics': topics, 'kbScore': score,
                'trainingReady': bool(training_ready), 'analysisMode': 'ai'}

    raise RuntimeError('OpenAI is not configured. Add OPENAI_API_KEY to backend/.env and restart Django.')
