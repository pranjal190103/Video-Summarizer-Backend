from django.http import HttpResponse


def home(request):
    return HttpResponse(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Video Summarizer API</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; background: #101827; color: #e8eef8; }
    body { box-sizing: border-box; display: grid; min-height: 100vh; margin: 0; padding: 2rem; place-items: center; }
    main { width: min(100%, 680px); padding: clamp(1.5rem, 5vw, 3rem); border: 1px solid #2b3a50; border-radius: 20px; background: #172235; }
    .eyebrow { color: #8eb6ff; font-size: .8rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
    h1 { margin: .6rem 0; font-size: clamp(2rem, 7vw, 3.2rem); }
    p, li { color: #b7c4d8; line-height: 1.7; }
    h2 { margin-top: 2rem; font-size: 1.1rem; }
    code { color: #a9c8ff; }
    a { color: #a9c8ff; }
    .note { padding: 1rem; border-radius: 10px; background: #202f45; }
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">Service online</div>
    <h1>Welcome to Video Summarizer</h1>
    <p>This backend powers video uploads, AI-generated summaries, transcripts, and SharePoint video imports.</p>
    <h2>API overview</h2>
    <p>The REST API is available under <code>/api/</code> and returns JSON.</p>
    <ul>
      <li><code>GET /api/health/</code> — check service health</li>
      <li><code>POST /api/auth/register/</code> and <code>POST /api/auth/login/</code> — create an account or sign in</li>
      <li><code>/api/videos/</code> — list videos or create a video record</li>
      <li><code>POST /api/videos/upload/</code> — upload a video for processing</li>
      <li><code>POST /api/analyze/</code> — analyze video content</li>
      <li><code>/api/sharepoint/</code> — manage SharePoint imports and sync</li>
    </ul>
    <p class="note">Most API endpoints require the token returned by login. Send it as an
      <code>Authorization: Token &lt;token&gt;</code> header.</p>
  </main>
</body>
</html>"""
    )
