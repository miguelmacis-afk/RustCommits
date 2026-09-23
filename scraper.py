import os
import json
import re
import time
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator, MyMemoryTranslator

# --- CONFIGURACIÓN ---
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
FACEPUNCH_URL = "https://commits.facepunch.com/r/rust_reboot"
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
    if not text or text.strip().lower() in [".", "...", "codegen"]:
        return text

    # 1. Intentar con GoogleTranslator manteniendo saltos de línea
    for attempt in range(3):
        try:
            translated = GoogleTranslator(source='auto', target='es').translate(text)
            if translated:
                time.sleep(1)
                return translated
        except Exception as e:
            print(f"[!] Google Translator error (Intento {attempt + 1}/3): {e}")
            time.sleep(2 * (attempt + 1))

    # 2. Proveedor de respaldo (MyMemory)
    try:
        print("[*] Probando proveedor de traducción de respaldo (MyMemory)...")
        translated = MyMemoryTranslator(source='en-US', target='es-ES').translate(text)
        if translated:
            return translated
    except Exception as e:
        print(f"[!] Error con proveedor de respaldo: {e}")

    return text

def clean_and_translate_repo(repo_str):
    if not repo_str:
        return "Rust"
    
    # 1. Eliminar prefijos comunes de ramas/repositorio
    cleaned = repo_str.replace("rust_reboot/main/", "").replace("main/", "")
    
    # 2. Eliminar el ID numérico final (ej: #16536, #165360)
    cleaned = re.sub(r'#\d+$', '', cleaned).strip()
    
    # 3. Reemplazar guiones bajos por espacios para mejorar la traducción
    cleaned_for_translation = cleaned.replace('_', ' ')
    
    # 4. Traducir el texto
    translated = translate_text(cleaned_for_translation)
    
    # 5. Aplicar formato de título (Mayúscula en cada palabra)
    return translated.title()

def clean_message(raw_text):
    # Eliminar contadores de reacciones
    cleaned = re.sub(r'thumb_up\s*\d+\s*thumb_down\s*\d+', '', raw_text, flags=re.IGNORECASE)
    # Limpiar espacios extra por línea manteniendo los saltos de línea estructurales (\n)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return '\n'.join(lines)

def extract_commit_message(card):
    # Intentar buscar el nodo específico del contenido del commit
    msg_el = card.select_one('.title, .commit-title, .message, .description, .text, blockquote')
    if msg_el and msg_el.get_text(strip=True):
        return clean_message(msg_el.get_text(separator='\n', strip=True))
    
    card_copy = BeautifulSoup(str(card), 'html.parser')
    for unneeded in card_copy.select('.author, .user-name, .repo, .repository, .date, .time, .avatar, img, video'):
        unneeded.decompose()
    
    # Usar '\n' como separador para conservar la estructura del mensaje
    clean_text = card_copy.get_text(separator='\n', strip=True)
    return clean_message(clean_text)

def extract_media(element):
    images, videos = [], []
    
    card_copy = BeautifulSoup(str(element), 'html.parser')
    for avatar_node in card_copy.select('.avatar, .user-avatar, .author, .user-name, .user, [class*="avatar"]'):
        avatar_node.decompose()
        
    AVATAR_TERMS = ['avatar', 'avatars', 'profile', 'gravatar', 'steamcommunity', '.svg']

    for img in card_copy.find_all('img'):
        src = img.get('src')
        if not src:
            continue
        src_lower = src.lower()
        if any(term in src_lower for term in AVATAR_TERMS):
            continue
            
        full_url = 'https:' + src if src.startswith('//') else ('https://commits.facepunch.com' + src if not src.startswith('http') else src)
        if full_url not in images:
            images.append(full_url)
            
    for video in card_copy.find_all(['video', 'source']):
        src = video.get('src')
        if src:
            full_url = 'https:' + src if src.startswith('//') else ('https://commits.facepunch.com' + src if not src.startswith('http') else src)
            if full_url not in videos:
                videos.append(full_url)
            
    text = card_copy.get_text()
    urls = re.findall(r'https?://[^\s]+\.(?:png|jpg|jpeg|gif|mp4|webm)', text)
    for url in urls:
        url_lower = url.lower()
        if any(term in url_lower for term in AVATAR_TERMS):
            continue
            
        if url.endswith(('.mp4', '.webm')) and url not in videos:
            videos.append(url)
        elif not url.endswith(('.mp4', '.webm')) and url not in images:
            images.append(url)
            
    return images, videos

def send_to_discord_batch(commits_batch):
    print(f"[*] Preparando envío de lote minimalista con {len(commits_batch)} commits a Discord...")
    embeds = []
    video_urls = []
    
    for commit in commits_batch:
        clean_repo = clean_and_translate_repo(commit['repo'])
        video_icon = " 🎬" if commit['videos'] else ""

        embed = {
            "color": 13517355,  # Color Rust (#CE422B)
            "author": {
                "name": f"👤 {commit['author']}"
            },
            "description": f"**{commit['translated_msg']}**\n\n🔀 `{clean_repo}`\n📌 [#{commit['id']}]({commit['url']}){video_icon}",
            "footer": {
                "text": "⚙️ Facepunch Rust Commits"
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        if commit['images']:
            embed["image"] = {"url": commit['images'][0]}

        embeds.append(embed)

        if commit['videos']:
            video_urls.extend(commit['videos'])

    payload = {"embeds": embeds}
    
    if video_urls:
        payload["content"] = "\n".join(video_urls)

    res = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    if res.status_code in [200, 204]:
        print(f"[+] Lote de {len(commits_batch)} commits enviado correctamente a Discord.")
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
        images, videos = extract_media(card)

        if images or videos:
            is_sig = True
            reason = f"Aprobado por multimedia ({len(images)} img, {len(videos)} vid)"
        else:
            is_sig, reason = is_significant(message)

        msg_preview = message[:50].replace('\n', ' ')
        print(f"[*] Analizando commit: {commit_id} de {author} | Msg: '{msg_preview}...'")
        
        if is_sig:
            print(f"  [+] APROBADO: {reason}")
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
