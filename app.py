import os
import re
import time
import json
import html
import shutil
import threading
import subprocess
import urllib.request
import urllib.error
from datetime import datetime

import pysubs2
from flask import Flask, render_template, request, redirect, url_for, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")

app = Flask(__name__, template_folder=WEB_DIR)


def env_to_bool(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


CONFIG = {
    "scan_folder": os.getenv("SCAN_FOLDER", "/media/in"),
    "output_folder": os.getenv("OUTPUT_FOLDER", "/media/out"),
    "interval": int(os.getenv("SCAN_INTERVAL", "60")),
    "translator_url": os.getenv("TRANSLATOR_URL", "http://192.168.50.46:5000"),
    "translate_subtitles": env_to_bool(os.getenv("TRANSLATE_SUBTITLES", "true"), True),
    "subtitle_stream_index": int(os.getenv("SUBTITLE_STREAM_INDEX", "0")),
    "delete_managed_file": env_to_bool(os.getenv("DELETE_MANAGED_FILE", "yes"), True),
}

LOG = []

STATE = {
    "current_step": "waiting",
    "running": True,
    "last_scan_time": None,
    "files_found": 0,
    "matched_files": 0,
    "processed_files": 0,
    "translated_files": 0,
    "success_files": 0,
    "failed_files": 0,
    "last_error": "",
    "next_scan_at": time.time() + CONFIG["interval"],
    "current_file": "",
    "queue_files": [],
    "active_queue_file": "",
    "translation_progress": 0,
    "translation_done": 0,
    "translation_total": 0,
    "libretranslate_online": False,
}

# Běžný single-episode SubsPlease release
# Např.:
# [SubsPlease] Mato Seihei no Slave S2 - 09 (1080p) [DDCFA8A6].mkv
# [SubsPlease] Yoroi Shin Den Samurai Troopers - 09 (1080p) [0CBAE049].mkv
MATCH_PATTERN = r'^\[SubsPlease\]\s+(.+?)\s+-\s+(\d+(?:\.\d+)?(?:v\d+)?)\s+\(1080p\)\s+\[(.*?)\](?:\.mkv)?$'

# Batch release, které chceme ignorovat
# Např.:
# [SubsPlease] Dorohedoro (01-12) (1080p) [Batch]
BATCH_PATTERN = r'^\[SubsPlease\]\s+(.+?)\s+\((\d+)\s*-\s*(\d+)\)\s+\(1080p\)\s+\[(.*?)\](?:\.mkv)?$'

# Sem si můžeš doplnit přesné názvy pro Plex.
# "id" je volitelné a může být např. "tmdb-123456" nebo "tvdb-123456"
SHOW_OVERRIDES = {
    # "Mato Seihei no Slave": {"title": "Mato Seihei no Slave", "year": 2024},
    # "Sousou no Frieren": {"title": "Sousou no Frieren", "year": 2023},
    # "Yoroi Shin Den Samurai Troopers": {"title": "Yoroi Shin Den Samurai Troopers", "year": 2026, "id": "tmdb-123456"},
}


def add_log(message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    LOG.append(line)
    if len(LOG) > 1000:
        LOG.pop(0)


def set_step(step: str):
    STATE["current_step"] = step


def set_active_file(filename: str = ""):
    STATE["current_file"] = filename


def reset_translation_progress():
    STATE["translation_progress"] = 0
    STATE["translation_done"] = 0
    STATE["translation_total"] = 0


def safe_name(name: str) -> str:
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        name = name.replace(ch, "_")
    return name.strip().rstrip(".")


def normalize_title_for_plex(title: str) -> str:
    title = title.strip()
    title = re.sub(r"\s+", " ", title)
    return safe_name(title)


def parse_release_name_for_plex(source_filename: str):
    """
    Převod single-episode SubsPlease releasu na data pro Plex.

    Příklady:
    [SubsPlease] Mato Seihei no Slave S2 - 09 (1080p) [HASH].mkv
      -> series_name=Mato Seihei no Slave, season=2, episode=9

    [SubsPlease] Yoroi Shin Den Samurai Troopers - 09 (1080p) [HASH].mkv
      -> series_name=Yoroi Shin Den Samurai Troopers, season=1, episode=9

    [SubsPlease] Mayonaka Heart S6 Tune - 09 (1080p) [HASH].mkv
      -> series_name=Mayonaka Heart S6 Tune, season=1, episode=9
         protože S6 není na konci názvu
    """
    if re.match(BATCH_PATTERN, source_filename, re.IGNORECASE):
        return None

    match = re.match(MATCH_PATTERN, source_filename, re.IGNORECASE)
    if not match:
        return None

    raw_series = match.group(1).strip()
    raw_episode = match.group(2).strip()
    raw_tag = match.group(3).strip()

    if raw_tag.lower() == "batch":
        return None

    episode_match = re.match(r'^(\d+)', raw_episode)
    if not episode_match:
        return None
    episode = int(episode_match.group(1))

    season = 1
    series_name = raw_series

    # Season bereme jen pokud je na konci názvu:
    # "Show S2" -> season 2
    # "Mayonaka Heart S6 Tune" -> season 1
    season_match = re.match(r'^(.*?)(?:\s+S(\d+))$', raw_series, re.IGNORECASE)
    if season_match:
        series_name = season_match.group(1).strip()
        season = int(season_match.group(2))

    series_name = normalize_title_for_plex(series_name)

    return {
        "series_name": series_name,
        "season": season,
        "episode": episode,
    }


def get_plex_show_info(parsed: dict):
    base_title = parsed["series_name"]
    override = SHOW_OVERRIDES.get(base_title, {})

    plex_title = normalize_title_for_plex(override.get("title", base_title))
    plex_year = override.get("year")
    plex_id = override.get("id")  # např. tmdb-123456 nebo tvdb-123456

    folder_name = plex_title
    file_title = plex_title

    if plex_year:
        folder_name = f"{plex_title} ({plex_year})"
        file_title = f"{plex_title} ({plex_year})"

    if plex_id:
        folder_name = f"{folder_name} {{{plex_id}}}"

    return {
        "folder_name": safe_name(folder_name),
        "file_title": safe_name(file_title),
        "year": plex_year,
        "id": plex_id,
    }


def build_target_paths(source_filename: str, output_dir: str):
    parsed = parse_release_name_for_plex(source_filename)
    if not parsed:
        return None

    plex_info = get_plex_show_info(parsed)

    target_folder = os.path.join(
        output_dir,
        plex_info["folder_name"],
        f"Season {parsed['season']:02d}"
    )

    base_filename = f"{plex_info['file_title']} - s{parsed['season']:02d}e{parsed['episode']:02d}"
    video_path = os.path.join(target_folder, f"{base_filename}.mkv")
    en_sub_path = os.path.join(target_folder, f"{base_filename}.en.ass")
    cs_sub_path = os.path.join(target_folder, f"{base_filename}.cs.ass")

    return {
        "parsed": parsed,
        "plex_info": plex_info,
        "target_folder": target_folder,
        "base_filename": base_filename,
        "video_path": video_path,
        "en_sub_path": en_sub_path,
        "cs_sub_path": cs_sub_path,
    }


def get_target_video_path(source_filename: str, output_dir: str):
    paths = build_target_paths(source_filename, output_dir)
    if not paths:
        return None
    return paths["video_path"]


def is_file_ready(file_path: str) -> bool:
    try:
        if not os.path.isfile(file_path):
            return False

        size1 = os.path.getsize(file_path)
        time.sleep(1)
        size2 = os.path.getsize(file_path)

        return size1 == size2
    except Exception:
        return False


def check_libretranslate_status() -> bool:
    try:
        url = CONFIG["translator_url"].rstrip("/") + "/health"
        req = urllib.request.Request(url, method="GET")

        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body)

        ok = parsed.get("status") == "ok"
        STATE["libretranslate_online"] = ok
        return ok

    except Exception:
        STATE["libretranslate_online"] = False
        return False


def translate_text(text: str, target_lang: str = "cs") -> str:
    url = CONFIG["translator_url"].rstrip("/") + "/translate"

    payload = {
        "q": text,
        "source": "auto",
        "target": target_lang.lower(),
        "format": "text"
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=120) as response:
        body = response.read().decode("utf-8")
        parsed = json.loads(body)
        translated = parsed.get("translatedText", text)

        translated = html.unescape(translated)
        translated = translated.replace("\r", "").strip()
        return translated


def protect_ass_content(text: str):
    placeholders = {}
    counter = 0

    def add_placeholder(value: str) -> str:
        nonlocal counter
        key = f"<<<ASS_{counter}>>>"
        placeholders[key] = value
        counter += 1
        return key

    text = re.sub(r"\{.*?\}", lambda m: add_placeholder(m.group(0)), text)
    text = text.replace("\\N", add_placeholder("\\N"))
    text = text.replace("\\n", add_placeholder("\\n"))
    text = text.replace("\\h", add_placeholder("\\h"))

    return text, placeholders


def restore_ass_content(text: str, placeholders: dict) -> str:
    for key, value in placeholders.items():
        text = text.replace(key, value)
    return text


def split_for_translation(text: str):
    return re.split(r"(<<<ASS_\d+>>>)", text)


def export_ass_subtitles(video_path: str, subtitle_output_path: str, stream_index: int = 0) -> bool:
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-map", f"0:s:{stream_index}",
            subtitle_output_path
        ]

        add_log("📝 Exportuji EN titulky")

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            add_log("❌ Export EN titulků selhal")
            if result.stderr:
                add_log(result.stderr[-500:])
            return False

        if not os.path.exists(subtitle_output_path):
            add_log("❌ Export EN titulků selhal, soubor nebyl vytvořen")
            return False

        add_log("✅ Export EN titulků byl úspěšně dokončen")
        return True

    except FileNotFoundError:
        add_log("❌ ffmpeg není nainstalovaný nebo není v PATH")
        return False
    except Exception as e:
        add_log(f"❌ Chyba při exportu EN titulků: {e}")
        return False


def translate_subtitle(subtitle_path: str, target_lang: str = "cs"):
    try:
        add_log("📖 Načítám soubor s EN titulky přes pysubs2")

        subs = pysubs2.load(subtitle_path, encoding="utf-8")

        dialogue_lines = [line for line in subs if hasattr(line, "text")]
        total_dialogues = len(dialogue_lines)

        STATE["translation_total"] = total_dialogues
        STATE["translation_done"] = 0
        STATE["translation_progress"] = 0

        if total_dialogues == 0:
            add_log("⚠️ V titulcích nebyly nalezeny žádné dialogy")

        add_log("🌍 Spouštím překlad EN titulků...")

        translated_dialogues = 0

        for line in dialogue_lines:
            original_text = line.text or ""

            if not original_text.strip():
                translated_dialogues += 1
                STATE["translation_done"] = translated_dialogues
                STATE["translation_progress"] = int((translated_dialogues / total_dialogues) * 100) if total_dialogues > 0 else 100
                continue

            try:
                protected_text, placeholders = protect_ass_content(original_text)
                parts = split_for_translation(protected_text)

                translated_parts = []

                for part in parts:
                    if not part:
                        continue

                    if part in placeholders:
                        translated_parts.append(part)
                        continue

                    if not part.strip():
                        translated_parts.append(part)
                        continue

                    translated_part = translate_text(part, target_lang)
                    translated_parts.append(translated_part)

                translated_text = "".join(translated_parts)
                translated_text = restore_ass_content(translated_text, placeholders)

                translated_text = translated_text.replace("\\ N", "\\N")
                translated_text = translated_text.replace("/N", "\\N")
                translated_text = translated_text.replace("\\ n", "\\n")
                translated_text = translated_text.replace("\\ h", "\\h")
                translated_text = translated_text.replace("\r", "").strip()

                line.text = translated_text

            except urllib.error.HTTPError as e:
                add_log(f"❌ HTTP chyba překladu: {e}")
                return None
            except urllib.error.URLError as e:
                add_log(f"❌ Nelze se připojit k LibreTranslate: {e}")
                return None
            except Exception as e:
                add_log(f"❌ Chyba při překladu textu: {e}")
                return None

            translated_dialogues += 1
            STATE["translation_done"] = translated_dialogues
            STATE["translation_progress"] = int((translated_dialogues / total_dialogues) * 100) if total_dialogues > 0 else 100

        # Plex-friendly language suffix
        # např. Show - s01e09.en.ass -> Show - s01e09.cs.ass
        if subtitle_path.lower().endswith(".en.ass"):
            translated_subtitle_path = subtitle_path[:-7] + ".cs.ass"
        else:
            translated_subtitle_path = subtitle_path.rsplit(".", 1)[0] + ".cs.ass"

        subs.save(translated_subtitle_path)

        add_log("✅ Překlad byl úspěšně dokončen a uložen")
        STATE["translation_progress"] = 100
        return translated_subtitle_path

    except Exception as e:
        add_log(f"❌ Chyba při překladu ASS titulků přes pysubs2: {e}")
        return None


def remove_from_queue(filename: str):
    STATE["queue_files"] = [f for f in STATE["queue_files"] if f != filename]
    if STATE["active_queue_file"] == filename:
        STATE["active_queue_file"] = ""


def translate_subtitles_for_video(video_path: str, subtitle_stream_index: int = 0) -> bool:
    video_filename = os.path.basename(video_path)
    folder = os.path.dirname(video_path)
    base_name = os.path.splitext(video_filename)[0]

    original_ass = os.path.join(folder, f"{base_name}.en.ass")

    add_log("🎬 === Spouštím překlad titulků ===")

    set_step("exporting_subtitles")
    set_active_file(video_filename)

    ok_export = export_ass_subtitles(video_path, original_ass, subtitle_stream_index)
    if not ok_export:
        return False

    set_step("translating")
    set_active_file(video_filename)

    translated_path = translate_subtitle(original_ass, "cs")
    if not translated_path:
        return False

    set_step("saving_subtitles")
    set_active_file(video_filename)

    return True


def delete_source_file(file_path: str):
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            add_log(f"🗑️ Původní soubor byl smazán: {os.path.basename(file_path)}")
            return True
        return False
    except Exception as e:
        add_log(f"⚠️ Nepodařilo se smazat původní soubor: {e}")
        return False


def process_file(file_path: str, output_dir: str) -> bool:
    filename = os.path.basename(file_path)

    set_step("copying")
    set_active_file(filename)
    STATE["active_queue_file"] = filename

    paths = build_target_paths(filename, output_dir)

    if not paths:
        add_log(f'⚠️ Soubor "{filename}" neodpovídá epizodnímu formátu nebo je to batch')
        remove_from_queue(filename)
        set_active_file("")
        return False

    parsed = paths["parsed"]
    plex_info = paths["plex_info"]
    target_folder = paths["target_folder"]
    new_file_path = paths["video_path"]
    en_sub_path = paths["en_sub_path"]
    cs_sub_path = paths["cs_sub_path"]
    new_filename = os.path.basename(new_file_path)

    os.makedirs(target_folder, exist_ok=True)

    add_log(f"📄 === Zpracovávám soubor: {filename} ===")
    add_log(f"📺 Plex seriál: {plex_info['folder_name']}")
    add_log(f"📚 Série: {parsed['season']:02d}")
    add_log(f"🎞️ Epizoda: {parsed['episode']:02d}")
    add_log(f"📝 Nový název: {new_filename}")

    if not is_file_ready(file_path):
        add_log(f'⏳ Soubor "{filename}" ještě není připravený nebo se stále kopíruje')
        set_active_file("")
        return False

    if os.path.exists(new_file_path):
        add_log(f'ℹ️ Cílový soubor už existuje, přeskakuji: "{new_filename}"')
        if CONFIG["delete_managed_file"]:
            delete_source_file(file_path)
        remove_from_queue(filename)
        set_active_file("")
        return True

    add_log("📝 Kopíruji soubor do výstupní složky")

    try:
        shutil.copy2(file_path, new_file_path)
        add_log("✅ Kopírování souboru bylo úspěšně dokončeno")
    except Exception as e:
        add_log(f"❌ Kopírování souboru selhalo: {e}")
        remove_from_queue(filename)
        set_active_file("")
        return False

    if CONFIG["translate_subtitles"]:
        ok_translated = translate_subtitles_for_video(
            new_file_path,
            CONFIG["subtitle_stream_index"]
        )

        if ok_translated:
            STATE["translated_files"] += 1
        else:
            add_log(f'❌ Překlad titulků selhal pro "{new_filename}"')
            remove_from_queue(filename)
            set_active_file("")
            return False

    add_log("=== 📄 Vyhodnocuji celý proces ===")

    if os.path.exists(new_file_path):
        add_log(f"✅ Soubor {new_filename} existuje")
    if os.path.exists(en_sub_path):
        add_log(f"✅ Soubor {os.path.basename(en_sub_path)} existuje")
    if os.path.exists(cs_sub_path):
        add_log(f"✅ Soubor {os.path.basename(cs_sub_path)} existuje")

    if CONFIG["delete_managed_file"]:
        delete_source_file(file_path)

    add_log("📝 Načítám další soubor z fronty")

    remove_from_queue(filename)
    set_active_file("")
    return True


def do_scan():
    scan_folder = CONFIG["scan_folder"]
    output_folder = CONFIG["output_folder"]

    set_step("loading_files")
    set_active_file("")
    STATE["active_queue_file"] = ""
    reset_translation_progress()

    translator_ok = check_libretranslate_status()
    if translator_ok:
        add_log("🟢 LibreTranslate je online")
    else:
        add_log("🔴 LibreTranslate je offline")

    add_log(f"📂 Prohlížím složku: {scan_folder}")

    if not os.path.exists(scan_folder):
        raise FileNotFoundError(f"Složka neexistuje: {scan_folder}")

    if not os.path.exists(output_folder):
        os.makedirs(output_folder, exist_ok=True)

    items = os.listdir(scan_folder)
    files = []

    for item in items:
        full_path = os.path.join(scan_folder, item)
        if os.path.isfile(full_path):
            files.append(full_path)

    STATE["files_found"] = 0
    STATE["matched_files"] = 0
    STATE["processed_files"] = 0
    STATE["translated_files"] = 0
    STATE["success_files"] = 0
    STATE["failed_files"] = 0
    STATE["queue_files"] = []
    STATE["active_queue_file"] = ""

    if not files:
        add_log("⚠️ Ve vstupní složce nebyly nalezeny žádné soubory")
        set_step("evaluating")
        set_active_file("")
        STATE["last_scan_time"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        STATE["last_error"] = ""
        return

    set_step("checking_name")
    set_active_file("")

    matched_files_list = []
    invalid_count = 0
    batch_count = 0
    process_candidates = []

    for file_path in files:
        filename = os.path.basename(file_path)

        if re.match(BATCH_PATTERN, filename, re.IGNORECASE):
            batch_count += 1
            add_log(f'📦 Batch release přeskočen: "{filename}"')
            continue

        parsed = parse_release_name_for_plex(filename)
        if not parsed:
            invalid_count += 1
            continue

        target_video_path = get_target_video_path(filename, output_folder)

        if target_video_path and os.path.exists(target_video_path):
            add_log(f'ℹ️ Už existuje v cíli, přeskakuji: "{filename}"')
            continue

        matched_files_list.append(filename)
        process_candidates.append(file_path)

    STATE["files_found"] = len(process_candidates)
    STATE["matched_files"] = len(process_candidates)
    STATE["queue_files"] = matched_files_list.copy()

    add_log(f"📊 Souborů odpovídajících vzoru: {len(process_candidates)}")
    add_log(f"📦 Batch releasů přeskočeno: {batch_count}")
    add_log(f"⚠️ Souborů neodpovídajících vzoru: {invalid_count}")

    processed_count = 0
    success_count = 0
    failed_count = 0

    for file_path in process_candidates:
        processed_count += 1

        ok = process_file(file_path, output_folder)
        if ok:
            success_count += 1
        else:
            failed_count += 1

        STATE["processed_files"] = processed_count
        STATE["success_files"] = success_count
        STATE["failed_files"] = failed_count

    set_step("evaluating")
    set_active_file("")
    STATE["active_queue_file"] = ""

    add_log("📄 Vyhodnocuji celý proces")
    add_log(f"✅ Úspěšně zpracováno: {success_count}")
    add_log(f"⚠️ Neúspěšně zpracováno: {failed_count}")
    add_log(f"✅ Přeložené titulky: {STATE['translated_files']}")

    STATE["processed_files"] = processed_count
    STATE["success_files"] = success_count
    STATE["failed_files"] = failed_count
    STATE["last_scan_time"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    STATE["last_error"] = ""


def scanner_loop():
    while True:
        if not STATE["running"]:
            time.sleep(1)
            continue

        try:
            set_step("waiting")
            set_active_file("")
            reset_translation_progress()
            STATE["next_scan_at"] = time.time() + CONFIG["interval"]

            while STATE["running"] and time.time() < STATE["next_scan_at"]:
                time.sleep(0.2)

            if not STATE["running"]:
                continue

            do_scan()

        except Exception as e:
            STATE["last_error"] = str(e)
            set_step("error")
            set_active_file("")
            add_log(f"❌ Chyba: {e}")

        finally:
            STATE["next_scan_at"] = time.time() + CONFIG["interval"]


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", config=CONFIG, show_overrides=SHOW_OVERRIDES)


@app.route("/status", methods=["GET"])
def status():
    remaining = 0
    if STATE["running"] and STATE["current_step"] == "waiting":
        remaining = max(0, int(STATE["next_scan_at"] - time.time()))

    return jsonify({
        "config": CONFIG,
        "state": {
            "current_step": STATE["current_step"],
            "running": STATE["running"],
            "last_scan_time": STATE["last_scan_time"],
            "files_found": STATE["files_found"],
            "matched_files": STATE["matched_files"],
            "processed_files": STATE["processed_files"],
            "translated_files": STATE["translated_files"],
            "success_files": STATE["success_files"],
            "failed_files": STATE["failed_files"],
            "last_error": STATE["last_error"],
            "remaining_seconds": remaining,
            "current_file": STATE["current_file"],
            "queue_files": STATE["queue_files"],
            "active_queue_file": STATE["active_queue_file"],
            "translation_progress": STATE["translation_progress"],
            "translation_done": STATE["translation_done"],
            "translation_total": STATE["translation_total"],
            "libretranslate_online": STATE["libretranslate_online"]
        },
        "log": LOG[-300:]
    })


@app.route("/save", methods=["POST"])
def save():
    scan_folder = request.form.get("scan_folder", "").strip()
    output_folder = request.form.get("output_folder", "").strip()
    interval = request.form.get("interval", "60").strip()
    translator_url = request.form.get("translator_url", "").strip()

    delete_managed_file_raw = request.form.get("delete_managed_file", "yes").strip().lower()
    delete_managed_file = delete_managed_file_raw in ("1", "true", "yes", "on")

    if not scan_folder:
        scan_folder = r"C:\Users\BAli1\Desktop\mini\media"

    if not output_folder:
        output_folder = r"C:\Users\BAli1\Desktop\mini\TV Shows"

    if not translator_url:
        translator_url = "http://192.168.50.235:5000"

    try:
        interval = int(interval)
        if interval < 1:
            interval = 60
    except ValueError:
        interval = 60

    CONFIG["scan_folder"] = scan_folder
    CONFIG["output_folder"] = output_folder
    CONFIG["interval"] = interval
    CONFIG["translator_url"] = translator_url
    CONFIG["delete_managed_file"] = delete_managed_file

    STATE["next_scan_at"] = time.time() + interval

    add_log("⚙️ Nastavení bylo uloženo")
    add_log(f"📥 Vstupní složka: {scan_folder}")
    add_log(f"📤 Výstupní složka: {output_folder}")
    add_log(f"🌍 LibreTranslate: {translator_url}")
    add_log(f"⏱️ Interval kontroly: {interval} s")
    add_log(f"🗑️ Mazat zpracovaný zdrojový soubor: {'ano' if delete_managed_file else 'ne'}")

    return redirect(url_for("index"))


@app.route("/start", methods=["POST"])
def start():
    STATE["running"] = True
    STATE["next_scan_at"] = time.time() + CONFIG["interval"]
    add_log("▶️ Smyčka byla spuštěna")
    return redirect(url_for("index"))


@app.route("/stop", methods=["POST"])
def stop():
    STATE["running"] = False
    set_active_file("")
    reset_translation_progress()
    add_log("⏸️ Smyčka byla pozastavena")
    return redirect(url_for("index"))


if __name__ == "__main__":
    add_log("🚀 Aplikace byla spuštěna")
    add_log(f"📥 Vstupní složka: {CONFIG['scan_folder']}")
    add_log(f"📤 Výstupní složka: {CONFIG['output_folder']}")
    add_log(f"🌍 LibreTranslate: {CONFIG['translator_url']}")
    add_log(f"🗑️ Mazat zpracovaný zdrojový soubor: {'ano' if CONFIG['delete_managed_file'] else 'ne'}")

    thread = threading.Thread(target=scanner_loop, daemon=True)
    thread.start()

    app.run(host="0.0.0.0", port=5001, debug=False)