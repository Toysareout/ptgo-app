"""
Screenshot-Analyzer: Beziehungs- & Kommunikationsanalyse
Analysiert Screenshots aus 4 Perspektiven und gibt ein gemeinsames Fazit.
"""

import os
import base64
import json
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Request, Form
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

# --- CONFIG ---
APP_SECRET = os.getenv("APP_SECRET", "dev-secret-screenshot-analyzer")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
UPLOAD_DIR = Path("/tmp/screenshot-analyzer-uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Screenshot-Analyzer")
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET)

# --- DIE 4 PERSPEKTIVEN ---
PERSPECTIVES = {
    "lodovico": {
        "name": "Lodovico Santana",
        "book": "Lob des Sexismus",
        "color": "#f85149",
        "icon": "L",
        "system_prompt": """Du bist ein Experte fuer die Lehren von Lodovico Santana aus "Lob des Sexismus".
Kernprinzipien die du anwendest:
- Maennlichkeit und Dominanz in der Kommunikation
- Push-Pull-Dynamik: Interesse zeigen und zurueckziehen im Wechsel
- Frame Control: Wer den Rahmen der Interaktion bestimmt, fuehrt
- Qualification: Lass die andere Person sich bei dir beweisen, nicht umgekehrt
- Keine Beduerftigkeit zeigen — Neediness ist der groesste Attraktor-Killer
- Sexuelle Polaritaet zwischen maskulin und feminin bewahren
- Shittests erkennen und bestehen (Agree & Amplify, Ignore, Pressure Flip)
- Direktheit und Ehrlichkeit statt Nice-Guy-Verhalten
- Keine uebertriebene Verfuegbarkeit — Knappheitsprinzip anwenden
- Frauen wollen gefuehrt werden, nicht gefragt werden
Analysiere den Screenshot und gib konkrete, direkte Ratschlaege basierend auf diesen Prinzipien."""
    },
    "rollo": {
        "name": "Rollo Tomassi",
        "book": "The Rational Male (alle Baende)",
        "color": "#58a6ff",
        "icon": "R",
        "system_prompt": """Du bist ein Experte fuer die Lehren von Rollo Tomassi aus "The Rational Male" (alle Baende).
Kernprinzipien die du anwendest:
- Hypergamie verstehen: Frauen suchen instinktiv den besten verfuegbaren Partner
- SMV (Sexual Market Value): Dein Wert bestimmt deine Optionen
- Iron Rules of Tomassi: z.B. "Frame is everything", "Never let a woman define the rules"
- Blue Pill vs Red Pill Dynamiken erkennen
- AFC-Verhalten (Average Frustrated Chump) identifizieren und vermeiden
- Oneitis vermeiden — keine emotionale Abhaengigkeit von einer Person
- Plate Theory: Optionen offen halten erhoeht deinen Wert
- Covert Contracts erkennen: Erwartungen, die nie ausgesprochen wurden
- Weibliche Kommunikation deuten: Was sie sagt vs. was sie meint vs. was sie tut
- Der Mann muss seine eigene Mission priorisieren, nicht die Beziehung
Analysiere den Screenshot und gib analytische Ratschlaege basierend auf diesen Prinzipien."""
    },
    "tate": {
        "name": "Andrew Tate",
        "book": "Die Bibel (Tate-Perspektive)",
        "color": "#f78166",
        "icon": "T",
        "system_prompt": """Du bist ein Experte fuer die Lehren von Andrew Tate.
Kernprinzipien die du anwendest:
- Selbstverbesserung und persoenliche Exzellenz stehen ueber allem
- Finanzielle Unabhaengigkeit und Erfolg als Grundlage
- Keine Zeit verschwenden mit Menschen die keinen Wert bringen
- Mentale Staerke und Disziplin in jeder Situation
- Klare Kommunikation ohne Entschuldigungen
- High Value Man Mentalitaet: Du bist der Preis, nicht sie
- Keine Toleranz fuer Respektlosigkeit
- Fokus auf Mission, Geld, Fitness — Beziehungen kommen danach
- Abundance Mindset: Es gibt immer andere Optionen
- Speed und Entschlossenheit: Nicht zoegern, handeln
Analysiere den Screenshot und gib direkte, kompromisslose Ratschlaege basierend auf diesen Prinzipien."""
    },
    "manson": {
        "name": "Mark Manson",
        "book": "Models & The Subtle Art (alle Baende)",
        "color": "#3fb950",
        "icon": "M",
        "system_prompt": """Du bist ein Experte fuer die Lehren von Mark Manson aus "Models", "The Subtle Art of Not Giving a F*ck" und "Everything Is F*cked".
Kernprinzipien die du anwendest:
- Vulnerability (Verletzlichkeit) ist Staerke, nicht Schwaeche
- Ehrlichkeit und Authentizitaet ueber Techniken und Manipulation
- Polarisierung: Lieber starke Reaktionen als Gleichgueltigkeit
- Non-Neediness: Dein Selbstwert haengt nicht von der Reaktion anderer ab
- Gesunde Grenzen setzen — was du tolerierst definiert dein Leben
- Nicht jeder muss dich moegen — und das ist gut so
- Invest in dich selbst, nicht in die Validierung anderer
- Demographics: Ziehe die richtigen Menschen an, nicht alle
- Pain is inevitable, suffering is optional — waehle deinen Kampf
- Echte Verbindung entsteht durch Ehrlichkeit, nicht durch Spielchen
Analysiere den Screenshot und gib ehrliche, bodenstaendige Ratschlaege basierend auf diesen Prinzipien."""
    }
}

CONSENSUS_PROMPT = """Du hast 4 verschiedene Analysen eines Screenshots erhalten, jeweils aus der Perspektive von:
1. Lodovico Santana (Lob des Sexismus)
2. Rollo Tomassi (The Rational Male)
3. Andrew Tate (direkte Maennlichkeit)
4. Mark Manson (authentische Verletzlichkeit)

Hier sind die 4 Analysen:

--- LODOVICO SANTANA ---
{lodovico}

--- ROLLO TOMASSI ---
{rollo}

--- ANDREW TATE ---
{tate}

--- MARK MANSON ---
{manson}

Erstelle jetzt ein GEMEINSAMES FAZIT — die Punkte, bei denen ALLE VIER uebereinstimmen wuerden.
Formuliere es als klare, direkte Handlungsanweisung.
Nenne auch die 1-2 wichtigsten Unterschiede zwischen den Perspektiven.
Antworte auf Deutsch. Sei direkt und konkret. Maximal 200 Woerter."""


# --- ANALYSE-FUNKTION ---
def _analyze_screenshot(image_data: bytes, media_type: str) -> dict:
    """Analysiert einen Screenshot aus allen 4 Perspektiven + Konsens."""
    if not HAS_ANTHROPIC or not ANTHROPIC_API_KEY:
        return {
            "error": "Kein ANTHROPIC_API_KEY gesetzt. Bitte als Umgebungsvariable setzen.",
            "perspectives": {},
            "consensus": ""
        }

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    b64_image = base64.standard_b64encode(image_data).decode("utf-8")

    results = {}

    # Jede Perspektive einzeln abfragen
    for key, perspective in PERSPECTIVES.items():
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=perspective["system_prompt"] + "\n\nAntworte immer auf Deutsch. Sei direkt und konkret. Maximal 150 Woerter.",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_image
                        }
                    },
                    {
                        "type": "text",
                        "text": "Analysiere diesen Screenshot. Was siehst du? Was laeuft gut, was laeuft schlecht? Was sollte die Person tun? Gib konkrete Ratschlaege."
                    }
                ]
            }]
        )
        results[key] = response.content[0].text

    # Konsens erstellen
    consensus_response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system="Du bist ein neutraler Analyst der verschiedene Perspektiven zusammenfuehrt. Antworte auf Deutsch.",
        messages=[{
            "role": "user",
            "content": CONSENSUS_PROMPT.format(**results)
        }]
    )
    consensus = consensus_response.content[0].text

    return {
        "perspectives": results,
        "consensus": consensus,
        "error": None
    }


# --- HTML UI ---
def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    background: #0d1117;
    color: #e6edf3;
    line-height: 1.6;
    min-height: 100vh;
}}
.container {{ max-width: 960px; margin: 0 auto; padding: 20px; }}

.header {{
    text-align: center;
    padding: 40px 20px 30px;
    margin-bottom: 30px;
}}
.header h1 {{
    font-size: 2em;
    background: linear-gradient(90deg, #f85149, #58a6ff, #f78166, #3fb950);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
}}
.header p {{ color: #8b949e; font-size: 1em; }}

/* Upload Area */
.upload-area {{
    background: #161b22;
    border: 2px dashed #30363d;
    border-radius: 16px;
    padding: 60px 20px;
    text-align: center;
    cursor: pointer;
    transition: all 0.3s;
    margin-bottom: 30px;
    position: relative;
}}
.upload-area:hover, .upload-area.dragover {{
    border-color: #58a6ff;
    background: #1a2233;
}}
.upload-area input[type="file"] {{
    position: absolute;
    inset: 0;
    opacity: 0;
    cursor: pointer;
}}
.upload-icon {{ font-size: 3em; margin-bottom: 12px; display: block; }}
.upload-text {{ color: #8b949e; font-size: 1em; }}

/* Preview */
.preview-area {{
    display: none;
    margin-bottom: 20px;
    text-align: center;
}}
.preview-area img {{
    max-width: 100%;
    max-height: 400px;
    border-radius: 12px;
    border: 1px solid #30363d;
}}

/* Button */
.btn {{
    display: inline-block;
    padding: 14px 40px;
    background: linear-gradient(135deg, #238636, #1f6feb);
    color: #fff;
    border: none;
    border-radius: 10px;
    font-size: 1.1em;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.2s;
    text-align: center;
    width: 100%;
    max-width: 400px;
}}
.btn:hover {{ opacity: 0.9; }}
.btn:disabled {{ opacity: 0.5; cursor: wait; }}

.btn-container {{ text-align: center; margin: 20px 0; }}

/* Results */
.perspectives-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 24px;
}}
@media (max-width: 700px) {{
    .perspectives-grid {{ grid-template-columns: 1fr; }}
}}

.perspective-card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 20px;
    transition: border-color 0.2s;
}}
.perspective-card:hover {{ border-color: var(--card-color); }}
.perspective-card h3 {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
    font-size: 1.05em;
}}
.perspective-icon {{
    width: 32px; height: 32px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 0.9em;
    color: #fff;
    flex-shrink: 0;
}}
.perspective-card .book {{ font-size: 0.8em; color: #8b949e; font-weight: normal; }}
.perspective-card .analysis {{ color: #c9d1d9; font-size: 0.93em; white-space: pre-wrap; }}

.consensus-box {{
    background: linear-gradient(135deg, #1a2233, #1a1a2e);
    border: 2px solid #58a6ff;
    border-radius: 14px;
    padding: 24px;
    margin-bottom: 24px;
}}
.consensus-box h2 {{
    color: #58a6ff;
    margin-bottom: 14px;
    font-size: 1.3em;
}}
.consensus-box .text {{ color: #e6edf3; white-space: pre-wrap; font-size: 0.95em; }}

/* Loading */
.loading {{
    display: none;
    text-align: center;
    padding: 60px 20px;
}}
.loading.active {{ display: block; }}
.spinner {{
    width: 50px; height: 50px;
    border: 4px solid #30363d;
    border-top: 4px solid #58a6ff;
    border-radius: 50%;
    animation: spin 1s linear infinite;
    margin: 0 auto 20px;
}}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.loading-text {{ color: #8b949e; }}

.error-box {{
    background: #2d1b1b;
    border: 1px solid #f85149;
    border-radius: 10px;
    padding: 20px;
    color: #f85149;
    margin-bottom: 20px;
}}

.new-btn {{
    display: inline-block;
    padding: 12px 30px;
    background: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 8px;
    text-decoration: none;
    font-weight: 600;
    transition: border-color 0.2s;
}}
.new-btn:hover {{ border-color: #58a6ff; }}
</style>
</head>
<body>
<div class="container">
{body}
</div>
</body>
</html>"""


# --- ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def home():
    body = """
<div class="header">
    <h1>SCREENSHOT-ANALYZER</h1>
    <p>Lade einen Screenshot hoch — 4 Perspektiven, 1 Fazit.</p>
</div>

<form id="uploadForm" action="/analyze" method="post" enctype="multipart/form-data">

    <div class="upload-area" id="uploadArea">
        <input type="file" name="screenshot" id="fileInput" accept="image/*" required>
        <span class="upload-icon">+</span>
        <div class="upload-text">Screenshot hierher ziehen oder klicken</div>
    </div>

    <div class="preview-area" id="previewArea">
        <img id="previewImg" src="" alt="Vorschau">
    </div>

    <div class="btn-container">
        <button type="submit" class="btn" id="submitBtn" disabled>Analysieren</button>
    </div>

    <div class="context-area" style="margin-top:20px;">
        <label for="context" style="color:#8b949e; display:block; margin-bottom:8px; font-size:0.9em;">
            Optionaler Kontext (Was ist die Situation? Wer schreibt wem?)
        </label>
        <textarea name="context" id="context" rows="3"
            style="width:100%; background:#161b22; border:1px solid #30363d; border-radius:8px;
            color:#e6edf3; padding:12px; font-size:0.95em; resize:vertical; font-family:inherit;"
            placeholder="z.B. 'Sie schreibt mir nach 3 Dates nicht zurueck...'"></textarea>
    </div>
</form>

<script>
const fileInput = document.getElementById('fileInput');
const uploadArea = document.getElementById('uploadArea');
const previewArea = document.getElementById('previewArea');
const previewImg = document.getElementById('previewImg');
const submitBtn = document.getElementById('submitBtn');
const form = document.getElementById('uploadForm');

fileInput.addEventListener('change', function() {
    if (this.files && this.files[0]) {
        const reader = new FileReader();
        reader.onload = e => {
            previewImg.src = e.target.result;
            previewArea.style.display = 'block';
            uploadArea.style.display = 'none';
            submitBtn.disabled = false;
        };
        reader.readAsDataURL(this.files[0]);
    }
});

['dragenter','dragover'].forEach(e => {
    uploadArea.addEventListener(e, ev => { ev.preventDefault(); uploadArea.classList.add('dragover'); });
});
['dragleave','drop'].forEach(e => {
    uploadArea.addEventListener(e, ev => { ev.preventDefault(); uploadArea.classList.remove('dragover'); });
});
uploadArea.addEventListener('drop', ev => {
    fileInput.files = ev.dataTransfer.files;
    fileInput.dispatchEvent(new Event('change'));
});

form.addEventListener('submit', function() {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Analysiere... (ca. 30 Sek.)';
});
</script>
"""
    return HTMLResponse(_page("Screenshot-Analyzer", body))


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(screenshot: UploadFile = File(...), context: str = Form("")):
    # Datei lesen
    image_data = await screenshot.read()
    media_type = screenshot.content_type or "image/png"

    if not media_type.startswith("image/"):
        error_body = '<div class="error-box">Bitte lade ein Bild hoch (PNG, JPG, etc.)</div><a href="/" class="new-btn">Zurueck</a>'
        return HTMLResponse(_page("Fehler", error_body))

    # Analyse durchfuehren
    result = _analyze_screenshot(image_data, media_type)

    if result.get("error"):
        error_body = f"""
<div class="header"><h1>SCREENSHOT-ANALYZER</h1></div>
<div class="error-box">{result["error"]}</div>
<div class="btn-container"><a href="/" class="new-btn">Neuer Screenshot</a></div>"""
        return HTMLResponse(_page("Fehler", error_body))

    # Perspektiven-Cards bauen
    cards_html = ""
    for key, perspective in PERSPECTIVES.items():
        analysis_text = result["perspectives"].get(key, "Keine Analyse verfuegbar.")
        cards_html += f"""
<div class="perspective-card" style="--card-color:{perspective['color']}">
    <h3>
        <span class="perspective-icon" style="background:{perspective['color']}">{perspective['icon']}</span>
        {perspective['name']}
        <span class="book">({perspective['book']})</span>
    </h3>
    <div class="analysis">{analysis_text}</div>
</div>"""

    # Preview des Screenshots
    b64_preview = base64.standard_b64encode(image_data).decode("utf-8")

    context_html = ""
    if context.strip():
        context_html = f'<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px;margin-bottom:20px;color:#8b949e;font-size:0.9em;"><strong>Dein Kontext:</strong> {context}</div>'

    body = f"""
<div class="header">
    <h1>ANALYSE-ERGEBNIS</h1>
    <p>4 Perspektiven, 1 gemeinsames Fazit</p>
</div>

<div style="text-align:center;margin-bottom:24px;">
    <img src="data:{media_type};base64,{b64_preview}"
         style="max-width:100%;max-height:300px;border-radius:12px;border:1px solid #30363d;">
</div>

{context_html}

<div class="consensus-box">
    <h2>GEMEINSAMES FAZIT</h2>
    <p style="color:#8b949e;font-size:0.85em;margin-bottom:10px;">Worauf sich Santana, Tomassi, Tate &amp; Manson einigen wuerden:</p>
    <div class="text">{result['consensus']}</div>
</div>

<div class="perspectives-grid">
    {cards_html}
</div>

<div class="btn-container">
    <a href="/" class="new-btn">Neuer Screenshot analysieren</a>
</div>
"""
    return HTMLResponse(_page("Analyse-Ergebnis", body))


# --- START ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
