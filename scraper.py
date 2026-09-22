import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

# --- CONFIGURACIÓN ---
# Ahora obtiene el webhook de las variables de entorno de GitHub
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
    try:
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def is_significant(message):
    msg_lower = message.lower().strip()
    if len(msg_lower) < 12 or msg_lower.startswith(("wip", "typo", "merge", "cleanup")):
        return False
    return any(kw in msg_lower for kw in KEYWORDS)

def translate_text(text):
    try:
        return GoogleTranslator(source='auto', target='es').translate(text)
    except Exception as e:
        print(f"Error en la traducción: {e}")
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
    embeds = []
    extra_content = []
    
    for commit in commits_batch:
        embed = {
            "title": f"Commit de {commit['author']} ({commit['repo']})",
            "url": commit['url'],
            "description": f"**Traducción:**\n{commit['translated_msg']}\n\n**Original:**\n```{commit['original_msg']}```",
            "color": 15258703
        }
        
        # Insertar la primera imagen en el embed
        if commit['images']:
            embed["image"] = {"url": commit['images'][0]}
            
        embeds.append(embed)
        
        # Recopilar enlaces a videos o imágenes adicionales (ya que Discord no reproduce video dentro del embed directamente)
        media_links = []
        if commit['videos']:
            media_links.append("**Vídeos:** " + " | ".join(commit['videos']))
        if len(commit['images']) > 1:
            media_links.append("**Más imgs:** " + " | ".join(commit['images'][1:]))
            
        if media_links:
            extra_content.append(f"🔗 **Extra de {commit['author']}**: " + " - ".join(media_links))

    # Construir el payload con todos los embeds del lote
    payload = {"embeds": embeds}
    if extra_content:
        payload["content"] = "\n".join(extra_content)
        
    res = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    if res.status_code not in [200, 204]:
        print(f"Error enviando a Discord ({res.status_code}): {res.text}")
    else:
        print(f"[+] Lote de {len(commits_batch)} commits enviado correctamente.")

def run_scraper():
    seen = load_seen()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    res = requests.get(FACEPUNCH_URL, headers=headers)
    if res.status_code != 200:
        print(f"Error al acceder a Facepunch: {res.status_code}")
        return

    soup = BeautifulSoup(res.text, 'html.parser')
    cards = soup.select('.commit-card, .commit, div[data-commit-id]')
    
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
        
        msg_el = card.select_one('.message, .description')
        message = msg_el.get_text(strip=True) if msg_el else ""

        if is_significant(message):
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
            
            # Si el lote alcanza el tamaño configurado, se envía y se vacía
            if len(batch) >= BATCH_SIZE:
                send_to_discord_batch(batch)
                batch = []
                time.sleep(2) # Respetar rate limits de la API

        seen.add(commit_id)

    # Enviar cualquier commit restante que no haya llenado un bloque de 5
    if batch:
        send_to_discord_batch(batch)

    save_seen(seen)

if __name__ == "__main__":
    run_scraper()
