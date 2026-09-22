import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

# --- CONFIGURACIÓN ---
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
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
    except Exception:
        print("[*] No se encontró cache previo. Se iniciará desde cero.")
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
    if not text:
        return text
    try:
        # Fijamos source='en' explícitamente para evitar fallos de autodetección
        translated = GoogleTranslator(source='en', target='es').translate(text)
        return translated if translated else text
    except Exception as e:
        print(f"[!] Error traduciendo '{text[:30]}...': {e}")
        return text

def clean_message(raw_text):
    """Limpia texto basura como contadores de reacciones o espacios extra."""
    # Eliminar texto de reacciones tipo 'thumb_up 0 thumb_down 0'
    cleaned = re.sub(r'thumb_up\s*\d+\s*thumb_down\s*\d+', '', raw_text, flags=re.IGNORECASE)
    return cleaned.strip()

def extract_commit_message(card):
    """Busca el mensaje del commit usando múltiples selectores o limpiando el nodo."""
    msg_el = card.select_one('.title, .commit-title, .message, .description, .text, p, blockquote, div.content')
    if msg_el and msg_el.get_text(strip=True):
        return clean_message(msg_el.get_text(strip=True))
    
    # Respaldo en caso de cambio de estructura HTML
    card_copy = BeautifulSoup(str(card), 'html.parser')
    for unneeded in card_copy.select('.author, .user-name, .repo, .repository, .date, .time, .avatar, img, video'):
        unneeded.decompose()
    
    clean_text = card_copy.get_text(separator=' ', strip=True)
    return clean_message(clean_text)

def extract_media(element):
    images, videos = [], []
    
    for img in element.find_all('img'):
        src = img.get('src')
        if src and not src.endswith('.svg') and 'avatar' not in src:
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
            "description": commit['translated_msg'],  # Solo incluye la traducción
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
        return

    seen = load_seen()
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    print(f"[*] Conectando a {FACEPUNCH_URL}...")
    res = requests.get(FACEPUNCH_URL, headers=headers)
    
    if res.status_code != 200:
        print(f"[!] Error HTTP al acceder a Facepunch: Código {res.status_code}")
        return

    soup = BeautifulSoup(res.text, 'html.parser')
    cards = soup.select('.commit-card, .commit, div[data-commit-id], a.commit')
    print(f"[*] HTML parseado. Se encontraron {len(cards)} tarjetas de commits en la web.")

    batch = []
    
    for card in reversed(cards):
        commit_id = card.get('data-commit-id') or card.get('id')
        link_el = card.find('a', href=re.compile(r'/\d+'))
        
        if not commit_id and link_el:
            commit_id = link_el['href'].strip('/')
            
        if not commit_id or commit_id in seen:
            continue

        author_el = card.select_one('.author, .user-name')
        author = author_el.get_text(strip=True) if author_el else "Desconocido"
        
        repo_el = card.select_one('.repo, .repository')
        repo = repo_el.get_text(strip=True) if repo_el else "Rust"
        
        message = extract_commit_message(card)

        print(f"[*] Analizando commit: {commit_id} de {author} | Msg: '{message[:50]}...'")
        
        is_sig, reason = is_significant(message)
        
        if is_sig:
            print(f"  [+] APROBADO: {reason}")
            images, videos = extract_media(card)
            translated = translate_text(message)

            batch.append({
                'id': commit_id,
                'author': author,
                'repo': repo,
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
            print(f"  [-] DESCARTADO: {reason}")

        seen.add(commit_id)

    if batch:
        print(f"[*] Enviando {len(batch)} commits finales...")
        send_to_discord_batch(batch)

    save_seen(seen)
    print("=== FIN DEL SCRAPER ===")

if __name__ == "__main__":
    run_scraper()
