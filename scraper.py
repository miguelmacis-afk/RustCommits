import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

# --- CONFIGURACIÓN ---
# Obtiene el webhook de las variables de entorno de GitHub
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
# URL corregida (Facepunch usa el shortname directamente, sin /r/)
FACEPUNCH_URL = "https://commits.facepunch.com/rust"
SEEN_FILE = "seen_commits.json"
BATCH_SIZE = 5

KEYWORDS = [
    "added", "new", "redesign", "system", "feature", "weapon",
    "vehicle", "map", "overhaul", "model", "monument", "rework",
    "sound", "anim", "npc", "ui", "crafting"
]

def load_seen():
    print(f"[*] Intentando cargar {SEEN_FILE}...")
    try:
        with open(SEEN_FILE, "r") as f:
            seen = set(json.load(f))
            print(f"[*] Éxito: {len(seen)} commits cacheados previamente.")
            return seen
    except FileNotFoundError:
        print("[*] No se encontró cache previo. Se creará uno nuevo.")
        return set()
    except Exception as e:
        print(f"[!] Error leyendo cache: {e}")
        return set()

def save_seen(seen):
    print(f"[*] Guardando {len(seen)} commits en {SEEN_FILE}...")
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)
    print("[*] Archivo guardado correctamente.")

def is_significant(message):
    msg_lower = message.lower().strip()
    if len(msg_lower) < 12:
        return False, "Demasiado corto"
    if msg_lower.startswith(("wip", "typo", "merge", "cleanup")):
        return False, "Palabra ignorada (wip/typo/merge/cleanup)"
    
    for kw in KEYWORDS:
        if kw in msg_lower:
            return True, f"Contiene palabra clave: '{kw}'"
            
    return False, "No contiene palabras clave significativas"

def translate_text(text):
    try:
        return GoogleTranslator(source='auto', target='es').translate(text)
    except Exception as e:
        print(f"[!] Error en la traducción: {e}")
        return text

def extract_media(element):
    images, videos = [], []
    
    for img in element.find_all('img'):
        src = img.get('src')
        if src and not src.endswith('.svg'):
            src = 'https:' + src if src.startswith('//') else ('https://commits.facepunch.com' + src if not src.startswith('http') else src)
            images.append(src)
            
    for video in element.find_all(['video', 'source']):
        src = video.get('src')
        if src:
            src = 'https:' + src if src.startswith('//') else ('https://commits.facepunch.com' + src if not src.startswith('http') else src)
            if src not in videos:
                videos.append(src)
            
    text = element.get_text()
    urls = re.findall(r'https?://[^\s]+\.(?:png|jpg|jpeg|gif|mp4|webm)', text)
    for url in urls:
        if url.endswith(('.mp4', '.webm')) and url not in videos:
            videos.append(url)
        elif url not in images and not url.endswith('.svg'):
            images.append(url)
            
    return images, videos

def send_to_discord_batch(commits_batch):
    print(f"[*] Preparando envío de lote con {len(commits_batch)} commits a Discord...")
    embeds = []
    extra_content = []
    
    for commit in commits_batch:
        embed = {
            "title": f"Commit de {commit['author']} ({commit['repo']})",
            "url": commit['url'],
            "description": f"**Traducción:**\n{commit['translated_msg']}\n\n**Original:**\n```{commit['original_msg']}```",
            "color": 15258703
        }
        
        if commit['images']:
            embed["image"] = {"url": commit['images'][0]}
            
        embeds.append(embed)
        
        media_links = []
        if commit['videos']:
            media_links.append("**Vídeos:** " + " | ".join(commit['videos']))
        if len(commit['images']) > 1:
            media_links.append("**Más imgs:** " + " | ".join(commit['images'][1:]))
            
        if media_links:
            extra_content.append(f"🔗 **Extra de {commit['author']}**: " + " - ".join(media_links))

    payload = {"embeds": embeds}
    if extra_content:
        payload["content"] = "\n".join(extra_content)
        
    res = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    if res.status_code in [200, 204]:
        print(f"[+] Lote de {len(commits_batch)} commits enviado correctamente.")
    else:
        print(f"[!] Error enviando a Discord ({res.status_code}): {res.text}")

def run_scraper():
    print("=== INICIANDO FACEPUNCH SCRAPER ===")
    
    if not DISCORD_WEBHOOK_URL:
        print("[!] ERROR CRÍTICO: No se encontró la variable DISCORD_WEBHOOK_URL.")
        print("[!] Verifica los Secrets en GitHub Actions.")
        return

    seen = load_seen()
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    print(f"[*] Conectando a {FACEPUNCH_URL}...")
    res = requests.get(FACEPUNCH_URL, headers=headers)
    
    if res.status_code != 200:
        print(f"[!] Error HTTP al acceder a Facepunch: Código {res.status_code}")
        return
    print("[*] Conexión HTTP exitosa.")

    soup = BeautifulSoup(res.text, 'html.parser')
    cards = soup.select('.commit-card, .commit, div[data-commit-id], a.commit')
    print(f"[*] HTML parseado. Se encontraron {len(cards)} tarjetas de commits en la web.")
    
    if len(cards) == 0:
        print("[!] ADVERTENCIA: Se encontraron 0 commits. Es posible que la web cargue los datos mediante JavaScript y BeautifulSoup no pueda verlos.")
        print("[!] HTML de la web (primeros 500 caracteres):")
        print(res.text[:500])
        return

    batch = []
    
    for card in reversed(cards):
        commit_id = card.get('data-commit-id') or card.get('id')
        link_el = card.find('a', href=re.compile(r'/\d+'))
        
        if not commit_id and link_el:
            commit_id = link_el['href'].strip('/')
            
        if not commit_id:
            print("[-] Tarjeta ignorada: No se pudo encontrar un ID de commit.")
            continue
            
        if commit_id in seen:
            print(f"[-] Omitido (ya visto): {commit_id}")
            continue

        author_el = card.select_one('.author, .user-name')
        author = author_el.get_text(strip=True) if author_el else "Desconocido"
        
        repo_el = card.select_one('.repo, .repository')
        repo = repo_el.get_text(strip=True) if repo_el else "Rust"
        
        msg_el = card.select_one('.message, .description')
        message = msg_el.get_text(strip=True) if msg_el else ""

        print(f"[*] Analizando commit nuevo: {commit_id} de {author}...")
        
        is_sig, reason = is_significant(message)
        
        if is_sig:
            print(f"  [+] APROBADO: {reason}")
            images, videos = extract_media(card)
            translated = translate_text(message)

            batch.append({
                'id': commit_id,
                'author': author,
                'repo': repo,
                'original_msg': message,
                'translated_msg': translated,
                'url': f"https://commits.facepunch.com/{commit_id}",
                'images': images,
                'videos': videos
            })
            
            if len(batch) >= BATCH_SIZE:
                send_to_discord_batch(batch)
                batch = []
                time.sleep(2)
        else:
            print(f"  [-] DESCARTADO: {reason} | Mensaje original: '{message}'")

        seen.add(commit_id)

    if batch:
        print(f"[*] Enviando los últimos {len(batch)} commits que no llenaron un lote entero.")
        send_to_discord_batch(batch)
    else:
        print("[*] No hay lotes pendientes por enviar.")

    save_seen(seen)
    print("=== FIN DEL SCRAPER ===")

if __name__ == "__main__":
    run_scraper()
