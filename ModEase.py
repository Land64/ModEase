import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import requests
from bs4 import BeautifulSoup
import os
import time
import threading
import re
import json
from collections import Counter

API_KEY = "you might want to you use your own api key. (from curse forge)"

BASE_API_URL = "https://api.curseforge.com/v1"
MODRINTH_API_BASE_URL = "https://api.modrinth.com/v2"
GAME_ID_MINECRAFT = 432

MODRINTH_HEADERS = {
    "User-Agent": "ProjectDownloaderGUI/1.1 (PythonScript)"
}

PROJECT_TYPES = {
    "mc-mods": {"classId": 6, "name": "mod"},
    "texture-packs": {"classId": 12, "name": "resource pack"},
    "resource-packs": {"classId": 12, "name": "resource pack"}
}

_CLASS_ID_TO_NAME_MAP = {}
for data_val in PROJECT_TYPES.values():
    if data_val["classId"] not in _CLASS_ID_TO_NAME_MAP:
        _CLASS_ID_TO_NAME_MAP[data_val["classId"]] = data_val["name"]
if 6 not in _CLASS_ID_TO_NAME_MAP: _CLASS_ID_TO_NAME_MAP[6] = "mod"
if 12 not in _CLASS_ID_TO_NAME_MAP: _CLASS_ID_TO_NAME_MAP[12] = "resource pack"

MODLOADER_MAP_CF_API = {
    "forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6,
    "any": 0, "none": 0
}
MODLOADER_CHOICES = ["forge", "fabric", "quilt", "neoforge", "any", "none"]


MODRINTH_TYPE_TO_CF_CLASSID = {
    "mod": 6, "resourcepack": 12, "shader": 12
}

MODRINTH_API_TYPE_TO_DISPLAY_NAME = {
    "mod": "Mod", "resourcepack": "Resource Pack", "shader": "Shader",
    "datapack": "Datapack", "plugin": "Plugin"
}

session = requests.Session()
session.headers.update({"x-api-key": API_KEY, "Accept": "application/json"})

MC_VERSION_INPUT_GLOBAL = ""
MC_VERSION_GLOBAL = ""
LOADER_GLOBAL = ""
LOADER_API_ID_GLOBAL = 0
DOWNLOAD_FOLDER_GLOBAL = ""
MISSED_ITEMS_GLOBAL = []


def parse_version_string_backend(v_str):
    """Converts a version string (e.g., '1.16.5') to a tuple of integers for comparison."""
    parts = []
    for part in re.split(r'[.-]', str(v_str)):
        numeric_prefix = ""
        for char_idx, char_val in enumerate(part):
            if char_val.isdigit():
                numeric_prefix += char_val
            else:
                if char_idx == 0 and len(part) > 1 and not numeric_prefix :
                    continue
                break
        if numeric_prefix:
            parts.append(int(numeric_prefix))
        elif not parts:
            parts.append(0)
    return tuple(parts) if parts else (0,)


def gui_log(message):
    """Safely logs a message to the GUI's log area."""
    if app and hasattr(app, 'log_message'):
        app.log_message(message)
    else: 
        print(f"GUI_LOG_FALLBACK: {message}")


def make_api_request_backend(url, params=None, use_cf_session=True, is_json=True):
    """Makes an API request and handles common errors, logging to GUI."""
    try:
        if use_cf_session:
            response = session.get(url, params=params, timeout=20)
        else:
            response = requests.get(url, params=params, headers=MODRINTH_HEADERS, timeout=20)
        response.raise_for_status()
        return response.json() if is_json else response.content
    except requests.exceptions.HTTPError as e:
        err_msg = f"API HTTP Error: {e.response.status_code} for {url}"
        try: err_msg += f" - {e.response.json().get('description', e.response.json().get('error', e.response.text))}"
        except: err_msg += f" - {e.response.text[:100]}"
        gui_log(err_msg)
    except requests.exceptions.RequestException as e:
        gui_log(f"Network Error: {e} for {url.split('/')[-1]}")
    return None

def get_project_type_from_url_backend(url):
    """Determines project type from a CurseForge URL."""
    for keyword, data_val in PROJECT_TYPES.items():
        if f"curseforge.com/minecraft/{keyword}/" in url.lower(): return data_val
    return None

def get_slug_from_url_backend(url):
    """Extracts slug from a Modrinth or CurseForge URL."""
    return url.rstrip("/").split("/")[-1]

def get_project_details_by_slug_backend(slug, class_id, original_source_url):
    """Gets CurseForge project details by slug and class ID."""
    params = {"gameId": GAME_ID_MINECRAFT, "slug": slug, "classId": class_id}
    data_val = make_api_request_backend(f"{BASE_API_URL}/mods/search", params=params)
    if data_val and data_val.get("data"):
        for proj in data_val["data"]:
            if proj["slug"].lower() == slug.lower(): return proj
    MISSED_ITEMS_GLOBAL.append({"name": slug, "url": original_source_url, "reason": f"CF project details not found (slug: {slug}, classId: {class_id})"})
    return None

def get_latest_compatible_file_info_backend(project_id_or_slug, project_name_api, project_source, cf_project_type_name_if_any, original_source_url):
    """Gets the latest compatible file info from CurseForge or Modrinth."""
    global MC_VERSION_GLOBAL, LOADER_GLOBAL, LOADER_API_ID_GLOBAL
    
    if project_source == 'curseforge':
        api_params = {"gameVersion": MC_VERSION_GLOBAL, "pageSize": 50}
        is_mod = cf_project_type_name_if_any and cf_project_type_name_if_any.lower() == "mod"
        if is_mod and LOADER_GLOBAL.lower() not in ["any", "none"]:
            api_params["modLoaderType"] = LOADER_API_ID_GLOBAL

        files_data = make_api_request_backend(f"{BASE_API_URL}/mods/{project_id_or_slug}/files", params=api_params)
        if files_data and files_data.get("data"):
            for file_info in sorted(files_data["data"], key=lambda x: x.get('fileDate', '1970-01-01'), reverse=True):
                v_match = any(MC_VERSION_GLOBAL == gv or str(gv).startswith(MC_VERSION_GLOBAL) or MC_VERSION_GLOBAL.startswith(str(gv)) for gv in file_info.get("gameVersions", []))
                if not v_match: continue
                if is_mod:
                    if LOADER_GLOBAL.lower() in ["any", "none"]: return file_info, 'curseforge'
                    loaders_lower = [str(l).lower() for l in file_info.get("modLoaders", [])]
                    if not loaders_lower or "any" in loaders_lower or LOADER_GLOBAL.lower() in loaders_lower: return file_info, 'curseforge'
                else: return file_info, 'curseforge'
        
        gui_log(f"Fallback CF: Searching all CF files for {project_name_api} ({cf_project_type_name_if_any or 'N/A'})")
        all_files_data = make_api_request_backend(f"{BASE_API_URL}/mods/{project_id_or_slug}/files", params={"pageSize": 200})
        if all_files_data and all_files_data.get("data"):
            for file_info in sorted(all_files_data["data"], key=lambda x: x.get('fileDate', '1970-01-01'), reverse=True):
                v_match = any(MC_VERSION_GLOBAL == gv or str(gv).startswith(MC_VERSION_GLOBAL) or MC_VERSION_GLOBAL.startswith(str(gv).split('-')[0]) for gv in file_info.get("gameVersions", []))
                if not v_match: continue
                if is_mod:
                    if LOADER_GLOBAL.lower() in ["any", "none"]: return file_info, 'curseforge'
                    loaders_lower = [str(l).lower() for l in file_info.get("modLoaders", [])]
                    if not loaders_lower or "any" in loaders_lower or LOADER_GLOBAL.lower() in loaders_lower: return file_info, 'curseforge'
                else: return file_info, 'curseforge'
        MISSED_ITEMS_GLOBAL.append({"name": project_name_api, "url": original_source_url, "reason": f"No compatible CF file (MC: {MC_VERSION_GLOBAL}, L: {LOADER_GLOBAL})"})
        return None, None
    
    elif project_source == 'modrinth':
        params = { "game_versions": json.dumps([MC_VERSION_GLOBAL]) }
        if LOADER_GLOBAL.lower() not in ["any", "none"]:
            params["loaders"] = json.dumps([LOADER_GLOBAL.lower()])

        versions_data = make_api_request_backend(f"{MODRINTH_API_BASE_URL}/project/{project_id_or_slug}/version", params=params, use_cf_session=False)
        time.sleep(0.05)
        if not versions_data and "loaders" in params:
            gui_log(f"Retrying Modrinth for {project_name_api} without loader filter.")
            del params["loaders"]
            versions_data = make_api_request_backend(f"{MODRINTH_API_BASE_URL}/project/{project_id_or_slug}/version", params=params, use_cf_session=False)
        
        if versions_data:
            for version_info in versions_data:
                if MC_VERSION_GLOBAL not in version_info.get("game_versions", []): continue
                if LOADER_GLOBAL.lower() not in ["any", "none"] and LOADER_GLOBAL.lower() not in [str(l).lower() for l in version_info.get("loaders", [])]: continue
                primary_file = next((f for f in version_info.get("files", []) if f.get("primary")), None)
                file_to_download = primary_file or (version_info.get("files")[0] if version_info.get("files") else None)
                if file_to_download:
                    return {
                        "fileName": file_to_download["filename"], "downloadUrl": file_to_download["url"],
                        "fileLength": file_to_download["size"]
                    }, 'modrinth'
        MISSED_ITEMS_GLOBAL.append({"name": project_name_api, "url": original_source_url, "reason": f"No compatible Modrinth file (MC: {MC_VERSION_GLOBAL}, L: {LOADER_GLOBAL})"})
        return None, None
    return None, None

def get_modrinth_slugs_from_collection_backend(collection_url):
    """Extracts project slugs from a Modrinth collection page."""
    gui_log(f"Fetching Modrinth collection: {collection_url}")
    html_content = make_api_request_backend(collection_url, use_cf_session=False, is_json=False)
    if not html_content:
        MISSED_ITEMS_GLOBAL.append({"name": "Modrinth Collection", "url": collection_url, "reason": "Failed to fetch or parse HTML"})
        return []
    soup = BeautifulSoup(html_content, "html.parser")
    slugs = set()
    for a_tag in soup.find_all("a", href=re.compile(r"^/(?:mod|plugin|resourcepack|shader|datapack|modpack)/[^/?#]+")):
        match = re.match(r"^/(?:mod|plugin|resourcepack|shader|datapack|modpack)/([^/?#]+)", a_tag['href'])
        if match and len(match.group(1)) > 1 :
            if a_tag.find_parent(class_=re.compile(r"(project-card|item|result|hit|flex-item)", re.I)):
                 slugs.add(match.group(1))
    if not slugs: gui_log(f"No project slugs robustly identified on Modrinth collection page: {collection_url}")
    else: gui_log(f"Found {len(slugs)} unique project slugs from Modrinth collection.")
    return list(slugs)

def get_modrinth_project_by_name_backend(project_name):
    """Searches Modrinth for a project by its display name."""
    gui_log(f"Searching Modrinth by name: '{project_name}'")
    search_params = {"query": project_name, "limit": 5, "index": "relevance", "facets": json.dumps([["project_type:mod"],["project_type:resourcepack"]])} 
    search_results = make_api_request_backend(f"{MODRINTH_API_BASE_URL}/search", params=search_params, use_cf_session=False)
    time.sleep(0.05)
    if search_results and search_results.get("hits"):
        for hit in search_results["hits"]:
            if hit.get("title", "").lower() == project_name.lower():
                gui_log(f"Found Modrinth project by name: '{hit['title']}' (Slug: {hit['slug']})")
                
                full_project_data = make_api_request_backend(f"{MODRINTH_API_BASE_URL}/project/{hit['slug']}", use_cf_session=False)
                return full_project_data
    gui_log(f"Modrinth project named '{project_name}' not found or no exact match.")
    return None


def find_curseforge_equivalent_backend(modrinth_slug):
    """Finds a CurseForge equivalent for a Modrinth project."""
    modrinth_page_url = f"https://modrinth.com/mod/{modrinth_slug}"
    gui_log(f"  Finding CF equivalent for Modrinth slug: {modrinth_slug}")
    mod_data = make_api_request_backend(f"{MODRINTH_API_BASE_URL}/project/{modrinth_slug}", use_cf_session=False)
    time.sleep(0.05)
    if not mod_data:
        MISSED_ITEMS_GLOBAL.append({"name": modrinth_slug, "url": modrinth_page_url, "reason": "Modrinth API project request failed"})
        return None, None, modrinth_page_url, modrinth_slug, "unknown"

    mod_display_name = mod_data.get('title', modrinth_slug)
    mod_project_type_api = mod_data.get('project_type', "unknown")
    cf_url = mod_data.get('external_resources', {}).get('curseforge')

    if cf_url and "curseforge.com/minecraft/" in cf_url:
        cf_slug_from_link = get_slug_from_url_backend(cf_url)
        cf_class_id = MODRINTH_TYPE_TO_CF_CLASSID.get(mod_project_type_api)
        if cf_class_id:
            return cf_slug_from_link, cf_class_id, modrinth_page_url, mod_display_name, mod_project_type_api
    
    gui_log(f"    No direct CF link for '{mod_display_name}'. Searching CF by name.")
    cf_class_id_search = MODRINTH_TYPE_TO_CF_CLASSID.get(mod_project_type_api)
    if not cf_class_id_search:
        MISSED_ITEMS_GLOBAL.append({"name": mod_display_name, "url": modrinth_page_url, "reason": f"Unsupported Modrinth type '{mod_project_type_api}' for CF search"})
        return None, None, modrinth_page_url, mod_display_name, mod_project_type_api

    params = {"gameId": GAME_ID_MINECRAFT, "classId": cf_class_id_search, "searchFilter": mod_display_name, "sortField": 2, "pageSize": 5}
    cf_results = make_api_request_backend(f"{BASE_API_URL}/mods/search", params=params)
    time.sleep(0.05)
    if cf_results and cf_results.get("data"):
        for cf_proj in cf_results["data"]:
            if cf_proj.get("name", "").lower() == mod_display_name.lower():
                return cf_proj['slug'], cf_proj['classId'], modrinth_page_url, mod_display_name, mod_project_type_api
    return None, None, modrinth_page_url, mod_display_name, mod_project_type_api

def download_worker_backend(file_info, project_type_name_display, original_source_url, source_api):
    """Downloads a file, logging progress to GUI."""
    global DOWNLOAD_FOLDER_GLOBAL
    dl_url, filename = file_info.get("downloadUrl"), file_info.get("fileName")
    file_len = file_info.get("fileLength", -1)

    if not dl_url:
        MISSED_ITEMS_GLOBAL.append({"name": filename or "Unknown", "url": original_source_url, "reason": "No download URL"})
        return
    filepath = os.path.join(DOWNLOAD_FOLDER_GLOBAL, filename)
    if os.path.exists(filepath) and (file_len == -1 or os.path.getsize(filepath) == file_len):
        gui_log(f"Exists: {filename}"); return

    gui_log(f"Downloading ({source_api}) {project_type_name_display}: {filename}")
    if app: app.update_progress_indeterminate()
    try:
        s = session if source_api == 'curseforge' else requests
        r = s.get(dl_url, stream=True, timeout=300, allow_redirects=True, headers=None if source_api == 'curseforge' else MODRINTH_HEADERS)
        r.raise_for_status()
        with open(filepath, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        gui_log(f"Downloaded: {filename}")
    except Exception as e:
        MISSED_ITEMS_GLOBAL.append({"name": filename, "url": original_source_url, "reason": f"Download error: {e}"})
    finally:
        if app: app.update_progress_determinate_step(stop_indeterminate=True)

def determine_and_set_best_mc_version_backend(projects_to_analyze):
    """Determines the 'best' (most common, latest) MC version from a list of projects."""
    global MC_VERSION_GLOBAL
    gui_log("Determining best Minecraft version...")
    if app: app.update_progress_indeterminate()
    all_supported_versions_flat = []
    
    total_projects_to_analyze = len(projects_to_analyze)
    for i, project in enumerate(projects_to_analyze):
        if app: app.set_progress((i + 1) / total_projects_to_analyze if total_projects_to_analyze > 0 else 0)
        project_id_or_slug = project['id_or_slug']
        source = project['source']
        project_name_for_log = project.get('name', project_id_or_slug)
        gui_log(f"  Analyzing versions for ({source}): {project_name_for_log}")
        time.sleep(0.05)

        project_versions = []
        if source == 'curseforge':
            cf_mod_id = project.get('cf_mod_id')
            if not cf_mod_id:
                gui_log(f"    Skipping CF project {project_name_for_log}, no numeric ID provided for version analysis.")
                continue
            files_data = make_api_request_backend(f"{BASE_API_URL}/mods/{cf_mod_id}/files", params={"pageSize": 500})
            if files_data and files_data.get("data"):
                for f_info in files_data["data"]:
                    project_versions.extend(f_info.get("gameVersions", []))
        elif source == 'modrinth':
            modrinth_versions_data = make_api_request_backend(f"{MODRINTH_API_BASE_URL}/project/{project_id_or_slug}/version", use_cf_session=False)
            if modrinth_versions_data:
                for v_info in modrinth_versions_data:
                    project_versions.extend(v_info.get("game_versions", []))
        
        unique_project_versions = set(str(v) for v in project_versions if re.match(r"^\d+(\.\d+)+(\.\d+)?(-\w+(\.\d+)?)?$", str(v))) 
        all_supported_versions_flat.extend(list(unique_project_versions))

    if not all_supported_versions_flat:
        gui_log("Could not determine best version (no version data found). Please specify a version.")
        if app: app.update_progress_determinate_step(stop_indeterminate=True)
        return False

    version_counts = Counter(all_supported_versions_flat)
    if not version_counts:
        gui_log("Could not determine best version (no valid versions counted). Please specify a version.")
        if app: app.update_progress_determinate_step(stop_indeterminate=True)
        return False
        
    max_support = max(version_counts.values())
    best_candidates = [v for v, count in version_counts.items() if count == max_support]
    
    best_candidates.sort(key=parse_version_string_backend, reverse=True)
    
    if best_candidates:
        MC_VERSION_GLOBAL = best_candidates[0]
        gui_log(f"Determined best Minecraft version: {MC_VERSION_GLOBAL} (supported by {max_support}/{len(projects_to_analyze)} main projects)")
        if app: app.update_mc_version_display(MC_VERSION_GLOBAL)
        if app: app.update_progress_determinate_step(stop_indeterminate=True)
        return True
    else:
        gui_log("Could not determine best version. No candidates found. Please specify a version.")
        if app: app.update_progress_determinate_step(stop_indeterminate=True)
        return False

def process_modlist_from_html_backend(modlist_path):
    """Processes mods from an HTML file."""
    global MISSED_ITEMS_GLOBAL, MC_VERSION_GLOBAL
    projects_for_best_version_analysis = []
    initial_project_items = []

    try:
        with open(modlist_path, "r", encoding="utf-8") as f: soup = BeautifulSoup(f.read(), "html.parser")
    except Exception as e:
        MISSED_ITEMS_GLOBAL.append({"name": "HTML Modlist", "url": modlist_path, "reason": f"Read error: {e}"})
        return
    
    raw_items = [{'url': a['href'], 'type_data': pt} for a in soup.find_all('a', href=True) if (pt := get_project_type_from_url_backend(a['href']))]
    if not raw_items: gui_log("No CF URLs in HTML."); return

    gui_log(f"Found {len(raw_items)} potential CF projects in HTML. Analyzing...")
    for item_data in raw_items:
        url, type_info = item_data["url"], item_data["type_data"]
        slug = get_slug_from_url_backend(url)
        details = get_project_details_by_slug_backend(slug, type_info["classId"], url)
        if details:
            initial_project_items.append({'details': details, 'type_name': type_info["name"], 'original_url': url, 'source': 'curseforge'})
            projects_for_best_version_analysis.append({'id_or_slug': slug, 'source': 'curseforge', 'cf_mod_id': details.get('id'), 'name': details.get('name')})
        time.sleep(0.05)
    
    if MC_VERSION_INPUT_GLOBAL.lower() == "best":
        if not projects_for_best_version_analysis:
            gui_log("No projects found to determine 'best' version from HTML. Please specify a version.")
            return
        if not determine_and_set_best_mc_version_backend(projects_for_best_version_analysis):
            return 
    else:
        MC_VERSION_GLOBAL = MC_VERSION_INPUT_GLOBAL
        gui_log(f"Using specified MC Version: {MC_VERSION_GLOBAL}")

    threads = []
    if app: app.set_progress_total_steps(len(initial_project_items))
    for i, item in enumerate(initial_project_items):
        if app: app.set_progress((i + 1) / len(initial_project_items) if initial_project_items else 0)
        details, type_n, original_url, source_api = item['details'], item['type_name'], item['original_url'], item['source']
        file_info, _ = get_latest_compatible_file_info_backend(details["id"], details["name"], source_api, type_n, original_url)
        if file_info:
            t = threading.Thread(target=download_worker_backend, args=(file_info, type_n, original_url, source_api))
            threads.append(t); t.start(); time.sleep(0.02)
    for t in threads: t.join()

def process_single_mod_and_dependencies_backend(cf_url):
    """Processes a single CurseForge mod and its dependencies."""
    global MISSED_ITEMS_GLOBAL, MC_VERSION_GLOBAL
    main_type = get_project_type_from_url_backend(cf_url)
    if not ("curseforge.com/minecraft/" in cf_url and main_type):
        MISSED_ITEMS_GLOBAL.append({"name": "Main Mod URL", "url": cf_url, "reason": "Invalid CF URL/type"}); return
    main_slug = get_slug_from_url_backend(cf_url)
    main_details = get_project_details_by_slug_backend(main_slug, main_type["classId"], cf_url)
    if not main_details: return

    projects_to_download_info = {}
    resolve_queue = [(main_details["id"], main_details.get("name", main_slug))] 
    visited_for_resolution = set()
    projects_for_best_version_analysis = []

    gui_log("Resolving dependencies...")
    while resolve_queue:
        current_mod_id, current_mod_name = resolve_queue.pop(0)
        if current_mod_id in visited_for_resolution: continue
        visited_for_resolution.add(current_mod_id)
        
        mod_api_resp = make_api_request_backend(f"{BASE_API_URL}/mods/{current_mod_id}"); time.sleep(0.05)
        if mod_api_resp and mod_api_resp.get("data"):
            p_data = mod_api_resp["data"]
            if p_data["id"] not in projects_to_download_info:
                 projects_to_download_info[p_data["id"]] = {
                    "id": p_data["id"], "name": p_data["name"], 
                    "type_name": _CLASS_ID_TO_NAME_MAP.get(p_data["classId"], "Project"), 
                    "url": p_data.get("links", {}).get("websiteUrl", f"CF_ID_{p_data['id']}"),
                    "source": "curseforge"
                 }
                 projects_for_best_version_analysis.append({'id_or_slug': p_data["slug"], 'source': 'curseforge', 'cf_mod_id': p_data["id"], 'name': p_data["name"]})

            for dep in p_data.get("dependencies", []):
                if dep["relationType"] in [2,3] and dep["modId"] not in visited_for_resolution:
                    resolve_queue.append((dep["modId"], f"Dependency of {p_data['name']}")) 
        else: MISSED_ITEMS_GLOBAL.append({"name": f"Dep ID {current_mod_id}", "url": f"CF_ID_{current_mod_id}", "reason": "Dep API fetch fail"})

    if MC_VERSION_INPUT_GLOBAL.lower() == "best":
        if not projects_for_best_version_analysis:
            gui_log("No projects found to determine 'best' version. Please specify a version.")
            return
        if not determine_and_set_best_mc_version_backend(projects_for_best_version_analysis):
            return
    else:
        MC_VERSION_GLOBAL = MC_VERSION_INPUT_GLOBAL
        gui_log(f"Using specified MC Version: {MC_VERSION_GLOBAL}")

    threads = []
    final_projects_list = list(projects_to_download_info.values())
    if app: app.set_progress_total_steps(len(final_projects_list))
    for i, p_info in enumerate(final_projects_list):
        if app: app.set_progress((i + 1) / len(final_projects_list) if final_projects_list else 0)
        f_info, _ = get_latest_compatible_file_info_backend(p_info['id'], p_info['name'], 'curseforge', p_info['type_name'], p_info['url'])
        if f_info:
            t = threading.Thread(target=download_worker_backend, args=(f_info, p_info['type_name'], p_info['url'], 'curseforge'))
            threads.append(t); t.start(); time.sleep(0.02)
    for t in threads: t.join()

def process_modrinth_collection_backend(collection_url):
    """Processes mods from a Modrinth collection, finding CF equivalents or using Modrinth."""
    global MISSED_ITEMS_GLOBAL, MC_VERSION_GLOBAL
    if not ("modrinth.com/collection/" in collection_url):
        MISSED_ITEMS_GLOBAL.append({"name": "Modrinth Collection URL", "url": collection_url, "reason": "Invalid URL"}); return

    modrinth_slugs = get_modrinth_slugs_from_collection_backend(collection_url)
    if not modrinth_slugs: gui_log("No slugs from Modrinth collection."); return
    
    projects_to_process_info = []
    projects_for_best_version_analysis = []

    gui_log(f"Analyzing {len(modrinth_slugs)} Modrinth projects for CF equivalents or direct download...")
    for mod_slug_modrinth in modrinth_slugs:
        cf_slug, cf_class_id, modrinth_page_url, mod_disp_name, mod_proj_type_api = find_curseforge_equivalent_backend(mod_slug_modrinth)
        
        if cf_slug and cf_class_id:
            details = get_project_details_by_slug_backend(cf_slug, cf_class_id, modrinth_page_url)
            if details:
                projects_to_process_info.append({
                    'id_or_slug': details['id'], 'name': details['name'], 'source': 'curseforge', 
                    'cf_project_type_name': _CLASS_ID_TO_NAME_MAP.get(details['classId'], "Project"), 
                    'original_url': modrinth_page_url
                })
                projects_for_best_version_analysis.append({'id_or_slug': cf_slug, 'source': 'curseforge', 'cf_mod_id': details['id'], 'name': details['name']})
            else:
                projects_to_process_info.append({
                    'id_or_slug': mod_slug_modrinth, 'name': mod_disp_name, 'source': 'modrinth', 
                    'modrinth_project_type_api': mod_proj_type_api, 'original_url': modrinth_page_url
                })
                projects_for_best_version_analysis.append({'id_or_slug': mod_slug_modrinth, 'source': 'modrinth', 'name': mod_disp_name})
        else:
            projects_to_process_info.append({
                'id_or_slug': mod_slug_modrinth, 'name': mod_disp_name, 'source': 'modrinth', 
                'modrinth_project_type_api': mod_proj_type_api, 'original_url': modrinth_page_url
            })
            projects_for_best_version_analysis.append({'id_or_slug': mod_slug_modrinth, 'source': 'modrinth', 'name': mod_disp_name})
        time.sleep(0.05)

    if MC_VERSION_INPUT_GLOBAL.lower() == "best":
        if not projects_for_best_version_analysis:
            gui_log("No projects found to determine 'best' version. Please specify a version.")
            return
        if not determine_and_set_best_mc_version_backend(projects_for_best_version_analysis):
            return
    else:
        MC_VERSION_GLOBAL = MC_VERSION_INPUT_GLOBAL
        gui_log(f"Using specified MC Version: {MC_VERSION_GLOBAL}")

    threads = []
    if app: app.set_progress_total_steps(len(projects_to_process_info))
    processed_identifiers_for_dl = set()

    for i, proj_info in enumerate(projects_to_process_info):
        if app: app.set_progress((i + 1) / len(projects_to_process_info) if projects_to_process_info else 0)
        identifier = f"{proj_info['source']}_{proj_info['id_or_slug']}"
        if identifier in processed_identifiers_for_dl: continue
        
        file_info, source_api_used = None, None
        if proj_info['source'] == 'curseforge':
            file_info, source_api_used = get_latest_compatible_file_info_backend(
                proj_info['id_or_slug'], proj_info['name'], 'curseforge', 
                proj_info['cf_project_type_name'], proj_info['original_url']
            )
            display_type = proj_info['cf_project_type_name']
        elif proj_info['source'] == 'modrinth':
            file_info, source_api_used = get_latest_compatible_file_info_backend(
                proj_info['id_or_slug'], proj_info['name'], 'modrinth', 
                None, proj_info['original_url']
            )
            display_type = f"Modrinth {MODRINTH_API_TYPE_TO_DISPLAY_NAME.get(proj_info['modrinth_project_type_api'], 'Project')}"
        
        if file_info and source_api_used:
            t = threading.Thread(target=download_worker_backend, args=(file_info, display_type, proj_info['original_url'], source_api_used))
            threads.append(t); t.start(); processed_identifiers_for_dl.add(identifier); time.sleep(0.02)
        else:
             gui_log(f"Could not find compatible file for {proj_info['name']} from {proj_info['source']}")
    
    for t in threads: t.join()

def process_flexible_source_download_backend(project_identifier):
    """Processes a single project, searching on CF and MR, and letting user choose if found on both."""
    global MISSED_ITEMS_GLOBAL, MC_VERSION_GLOBAL
    
    MC_VERSION_GLOBAL = MC_VERSION_INPUT_GLOBAL 
    gui_log(f"Using specified MC Version: {MC_VERSION_GLOBAL}")

    cf_project_details = None
    mr_project_details = None
    
    is_cf_url = "curseforge.com/minecraft/" in project_identifier.lower()
    is_mr_url = "modrinth.com/" in project_identifier.lower() and "/mod/" in project_identifier.lower() 

    project_name_to_search = project_identifier 

    if is_cf_url:
        cf_type_data = get_project_type_from_url_backend(project_identifier)
        if cf_type_data:
            cf_slug = get_slug_from_url_backend(project_identifier)
            gui_log(f"Input is CurseForge URL. Slug: {cf_slug}")
            cf_project_details = get_project_details_by_slug_backend(cf_slug, cf_type_data["classId"], project_identifier)
            if cf_project_details:
                project_name_to_search = cf_project_details["name"]
                mr_project_details = get_modrinth_project_by_name_backend(project_name_to_search)
        else:
            gui_log(f"Could not determine project type from CurseForge URL: {project_identifier}")
    elif is_mr_url:
        mr_slug = get_slug_from_url_backend(project_identifier)
        gui_log(f"Input is Modrinth URL. Slug: {mr_slug}")
        mr_project_details = make_api_request_backend(f"{MODRINTH_API_BASE_URL}/project/{mr_slug}", use_cf_session=False)
        if mr_project_details:
            project_name_to_search = mr_project_details["title"]
            cf_class_id = MODRINTH_TYPE_TO_CF_CLASSID.get(mr_project_details.get("project_type"))
            if cf_class_id:
                
                params = {"gameId": GAME_ID_MINECRAFT, "classId": cf_class_id, "searchFilter": project_name_to_search, "sortField": 2, "pageSize": 1}
                cf_search_results = make_api_request_backend(f"{BASE_API_URL}/mods/search", params=params)
                if cf_search_results and cf_search_results.get("data"):
                    
                    potential_cf = cf_search_results["data"][0]
                    if potential_cf.get("name", "").lower() == project_name_to_search.lower():
                         cf_project_details = potential_cf 
            else: 
                 gui_log(f"Cannot map Modrinth type '{mr_project_details.get('project_type')}' to CF class for cross-search.")


    else: 
        gui_log(f"Input is a project name: {project_name_to_search}. Searching both platforms.")
        
        
        params_cf = {"gameId": GAME_ID_MINECRAFT, "classId": 6, "searchFilter": project_name_to_search, "sortField": 2, "pageSize": 1}
        cf_search_results = make_api_request_backend(f"{BASE_API_URL}/mods/search", params=params_cf)
        if cf_search_results and cf_search_results.get("data"):
            
            for res in cf_search_results.get("data", []):
                if res.get("name", "").lower() == project_name_to_search.lower():
                    cf_project_details = res
                    break
            if not cf_project_details: 
                 cf_project_details = cf_search_results["data"][0] if cf_search_results["data"] else None
        
        mr_project_details = get_modrinth_project_by_name_backend(project_name_to_search)

    chosen_source = None
    project_info_for_download = None

    if cf_project_details and mr_project_details:
        gui_log(f"Project '{project_name_to_search}' found on BOTH platforms:")
        gui_log(f"  [1] CurseForge: {cf_project_details.get('name', 'N/A')} (ID: {cf_project_details.get('id', 'N/A')})")
        gui_log(f"  [2] Modrinth: {mr_project_details.get('title', 'N/A')} (Slug: {mr_project_details.get('slug', 'N/A')})")
        
        app.user_choice_event.clear()
        app.user_choice_value = None
        app.prompt_for_user_choice(
            f"Choose source for '{project_name_to_search}':\n1 for CurseForge, 2 for Modrinth",
            [("1", "CurseForge"), ("2", "Modrinth")]
        )
        app.user_choice_event.wait(timeout=300) 

        if app.user_choice_value == "1":
            chosen_source = "curseforge"
        elif app.user_choice_value == "2":
            chosen_source = "modrinth"
        else:
            gui_log("No valid choice made or timeout. Aborting download for this item.")
            MISSED_ITEMS_GLOBAL.append({"name": project_name_to_search, "url": project_identifier, "reason": "User did not choose a source or choice timed out."})
            return
            
    elif cf_project_details:
        chosen_source = "curseforge"
        gui_log(f"Project '{cf_project_details.get('name', project_name_to_search)}' found only on CurseForge. Proceeding.")
    elif mr_project_details:
        chosen_source = "modrinth"
        gui_log(f"Project '{mr_project_details.get('title', project_name_to_search)}' found only on Modrinth. Proceeding.")
    else:
        gui_log(f"Project '{project_name_to_search}' not found on CurseForge or Modrinth.")
        MISSED_ITEMS_GLOBAL.append({"name": project_name_to_search, "url": project_identifier, "reason": "Not found on either platform."})
        return

    file_info_to_download = None
    source_api_used = None
    display_type_name = "Unknown Project"

    if chosen_source == "curseforge" and cf_project_details:
        
        cf_id = cf_project_details.get('id')
        cf_name = cf_project_details.get('name', 'Unknown CF Project')
        cf_class_id = cf_project_details.get('classId', 6) 
        cf_type_name = _CLASS_ID_TO_NAME_MAP.get(cf_class_id, "Project")
        display_type_name = cf_type_name
        if cf_id:
            file_info_to_download, source_api_used = get_latest_compatible_file_info_backend(
                cf_id, cf_name, 'curseforge', cf_type_name, project_identifier
            )
        else:
            gui_log(f"Could not get CurseForge ID for {cf_name}.")
            MISSED_ITEMS_GLOBAL.append({"name": cf_name, "url": project_identifier, "reason": "CF ID missing after search."})

    elif chosen_source == "modrinth" and mr_project_details:
        mr_slug = mr_project_details.get('slug')
        mr_title = mr_project_details.get('title', 'Unknown Modrinth Project')
        mr_project_type = mr_project_details.get('project_type', 'mod')
        display_type_name = f"Modrinth {MODRINTH_API_TYPE_TO_DISPLAY_NAME.get(mr_project_type, 'Project')}"
        if mr_slug:
            file_info_to_download, source_api_used = get_latest_compatible_file_info_backend(
                mr_slug, mr_title, 'modrinth', None, project_identifier
            )
        else:
            gui_log(f"Could not get Modrinth slug for {mr_title}.")
            MISSED_ITEMS_GLOBAL.append({"name": mr_title, "url": project_identifier, "reason": "Modrinth slug missing."})

    if file_info_to_download and source_api_used:
        if app: app.set_progress_total_steps(1) 
        if app: app.set_progress(0.5) 
        download_worker_backend(file_info_to_download, display_type_name, project_identifier, source_api_used)
        if app: app.set_progress(1)
    else:
        gui_log(f"No compatible file found for '{project_name_to_search}' from chosen source '{chosen_source}'.")
        
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ModEase")
        self.geometry("750x800")
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        icon_path_ico = "ModEase.ico"
        if os.path.exists(icon_path_ico):
            try:
                self.iconbitmap(icon_path_ico)
            except Exception as e:
                print(f"Could not set .ico icon: {e}")
        else:
            print(f"Icon file not found: {icon_path_ico} (for .ico)")

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(6, weight=1)

        self.mode_label = ctk.CTkLabel(self, text="Select Mode:")
        self.mode_label.grid(row=0, column=0, padx=10, pady=(10,0), sticky="w")
        self.mode_var = ctk.StringVar(value="HTML Modlist")
        self.mode_options = ["HTML Modlist", "Single Mod + Dependencies (CurseForge)", "Modrinth Collection", "Flexible Source Download"]
        self.mode_menu = ctk.CTkOptionMenu(self, variable=self.mode_var, values=self.mode_options, command=self.update_input_label_and_browse)
        self.mode_menu.grid(row=0, column=1, columnspan=2, padx=10, pady=(10,0), sticky="ew")

        self.input_path_label = ctk.CTkLabel(self, text="Modlist HTML File:")
        self.input_path_label.grid(row=1, column=0, padx=10, pady=5, sticky="w")
        self.input_path_entry = ctk.CTkEntry(self, placeholder_text="Path or URL or Project Name")
        self.input_path_entry.grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        self.browse_button = ctk.CTkButton(self, text="Browse", command=self.browse_file_or_folder)
        self.browse_button.grid(row=1, column=2, padx=10, pady=5)

        self.mc_version_label = ctk.CTkLabel(self, text="Minecraft Version (or 'best'):")
        self.mc_version_label.grid(row=2, column=0, padx=10, pady=5, sticky="w")
        self.mc_version_entry = ctk.CTkEntry(self, placeholder_text="e.g., 1.20.1 or best (not for Flexible mode)")
        self.mc_version_entry.grid(row=2, column=1, columnspan=2, padx=10, pady=5, sticky="ew")

        self.loader_label = ctk.CTkLabel(self, text="Mod Loader:")
        self.loader_label.grid(row=3, column=0, padx=10, pady=5, sticky="w")
        self.loader_var = ctk.StringVar(value=MODLOADER_CHOICES[0])
        self.loader_menu = ctk.CTkOptionMenu(self, variable=self.loader_var, values=MODLOADER_CHOICES)
        self.loader_menu.grid(row=3, column=1, columnspan=2, padx=10, pady=5, sticky="ew")

        self.download_folder_label = ctk.CTkLabel(self, text="Download Folder:")
        self.download_folder_label.grid(row=4, column=0, padx=10, pady=5, sticky="w")
        self.download_folder_entry = ctk.CTkEntry(self, placeholder_text="Select download destination")
        self.download_folder_entry.grid(row=4, column=1, padx=10, pady=5, sticky="ew")
        self.download_folder_button = ctk.CTkButton(self, text="Select Folder", command=self.select_download_dir)
        self.download_folder_button.grid(row=4, column=2, padx=10, pady=5)
        
        self.choice_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.choice_frame.grid(row=5, column=0, columnspan=3, padx=10, pady=5, sticky="ew")
        self.choice_frame.grid_remove() 
        
        self.choice_prompt_label = ctk.CTkLabel(self.choice_frame, text="", wraplength=680)
        self.choice_prompt_label.pack(pady=(0,5))
        self.choice_input_entry = ctk.CTkEntry(self.choice_frame, placeholder_text="Enter choice")
        self.choice_input_entry.pack(side=tk.LEFT, padx=(0,5), fill=tk.X, expand=True)
        self.choice_submit_button = ctk.CTkButton(self.choice_frame, text="Submit Choice", command=self.submit_user_choice)
        self.choice_submit_button.pack(side=tk.LEFT)
        self.user_choice_event = threading.Event()
        self.user_choice_value = None
        self.valid_choices_for_prompt = []


        self.log_textbox = ctk.CTkTextbox(self, wrap="word", state="disabled", height=200)
        self.log_textbox.grid(row=6, column=0, columnspan=3, padx=10, pady=10, sticky="nsew")

        self.progress_bar = ctk.CTkProgressBar(self, orientation="horizontal", mode="determinate")
        self.progress_bar.set(0)
        self.progress_bar.grid(row=7, column=0, columnspan=3, padx=10, pady=5, sticky="ew")

        self.start_button = ctk.CTkButton(self, text="Start Processing", command=self.start_processing_thread)
        self.start_button.grid(row=8, column=0, columnspan=3, padx=10, pady=10)
        
        self.update_input_label_and_browse()

    def update_mc_version_display(self, version_str):
        """Updates the MC version entry if 'best' was used."""
        if self.mc_version_entry.get().lower() == "best":
            current_text = self.mc_version_entry.get()
            if "Best:" not in current_text: 
                self.mc_version_entry.delete(0, tk.END)
                self.mc_version_entry.insert(0, f"Best: {version_str}")


    def update_input_label_and_browse(self, _=None):
        """Updates the input label and browse button state based on mode."""
        mode = self.mode_var.get()
        if mode == "HTML Modlist":
            self.input_path_label.configure(text="Modlist HTML File:")
            self.browse_button.configure(state="normal", text="Browse File")
        elif mode == "Single Mod + Dependencies (CurseForge)":
            self.input_path_label.configure(text="CurseForge Mod URL:")
            self.browse_button.configure(state="disabled", text="Browse")
        elif mode == "Modrinth Collection":
            self.input_path_label.configure(text="Modrinth Collection URL:")
            self.browse_button.configure(state="disabled", text="Browse")
        elif mode == "Flexible Source Download":
            self.input_path_label.configure(text="Project Name or URL (CF/MR):")
            self.browse_button.configure(state="disabled", text="Browse")


    def browse_file_or_folder(self):
        """Handles file browsing for HTML modlist mode."""
        mode = self.mode_var.get()
        path = ""
        if mode == "HTML Modlist":
            path = filedialog.askopenfilename(title="Select Modlist HTML File", filetypes=(("HTML files", "*.html"), ("All files", "*.*")))
        if path:
            self.input_path_entry.delete(0, tk.END)
            self.input_path_entry.insert(0, path)
            
    def select_download_dir(self):
        """Opens a dialog to select the download directory."""
        folder_path = filedialog.askdirectory(title="Select Folder to Download Mods Into")
        if folder_path:
            self.download_folder_entry.delete(0, tk.END)
            self.download_folder_entry.insert(0, folder_path)

    def log_message(self, message):
        """Appends a message to the log textbox in a thread-safe way."""
        def _log():
            self.log_textbox.configure(state="normal")
            self.log_textbox.insert(tk.END, str(message) + "\n")
            self.log_textbox.see(tk.END)
            self.log_textbox.configure(state="disabled")
        if self._check_thread_safety():
            self.after(0, _log)
        else: 
             _log() 

    def _check_thread_safety(self):
        return threading.current_thread() is threading.main_thread() or hasattr(self, '_w')

    def set_progress_total_steps(self, total_steps):
        """Sets the total steps for the progress bar."""
        self._total_steps = total_steps
        self._current_step = 0
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(0)

    def update_progress_determinate_step(self, stop_indeterminate=False):
        """Increments the progress bar by one step."""
        if hasattr(self, '_total_steps') and self._total_steps > 0:
            self._current_step +=1
            progress_val = self._current_step / self._total_steps if self._total_steps > 0 else 0
            self.progress_bar.set(min(progress_val, 1.0))
        if stop_indeterminate and self.progress_bar.cget("mode") == "indeterminate":
            self.progress_bar.configure(mode="determinate")
            self.progress_bar.set(1)

    def update_progress_indeterminate(self):
        """Sets the progress bar to indeterminate mode and starts animation."""
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()

    def stop_progress_indeterminate(self):
        """Stops indeterminate progress and sets mode to determinate."""
        if self.progress_bar.cget("mode") == "indeterminate":
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")

    def set_progress(self, value):
        """Sets the progress bar to a specific value (0.0 to 1.0)."""
        if self.progress_bar.cget("mode") == "indeterminate":
             self.progress_bar.stop()
             self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(value)

    def prompt_for_user_choice(self, prompt_text, choices_map):
        """Shows the choice input UI."""
        def _show():
            self.choice_prompt_label.configure(text=prompt_text)
            self.valid_choices_for_prompt = [c[0] for c in choices_map] 
            self.choice_input_entry.delete(0, tk.END)
            self.choice_frame.grid()
            self.choice_input_entry.focus()
        self.after(0, _show)

    def submit_user_choice(self):
        """Handles submission of user's choice from the special input field."""
        choice = self.choice_input_entry.get().strip()
        if choice in self.valid_choices_for_prompt:
            self.user_choice_value = choice
            self.log_message(f"User chose option: {choice}")
        else:
            self.user_choice_value = None 
            self.log_message(f"Invalid choice '{choice}'. Expected one of {self.valid_choices_for_prompt}.")
        
        self.choice_frame.grid_remove()
        self.user_choice_event.set()


    def start_processing_thread(self):
        """Starts the backend processing in a new thread after validating inputs."""
        global DOWNLOAD_FOLDER_GLOBAL, MC_VERSION_INPUT_GLOBAL, LOADER_GLOBAL, LOADER_API_ID_GLOBAL, MISSED_ITEMS_GLOBAL
        MISSED_ITEMS_GLOBAL = []

        input_path_or_name = self.input_path_entry.get().strip()
        DOWNLOAD_FOLDER_GLOBAL = self.download_folder_entry.get().strip()
        MC_VERSION_INPUT_GLOBAL = self.mc_version_entry.get().strip()
        LOADER_GLOBAL = self.loader_var.get()
        LOADER_API_ID_GLOBAL = MODLOADER_MAP_CF_API.get(LOADER_GLOBAL, 0)
        current_mode = self.mode_var.get()

        if not input_path_or_name:
            messagebox.showerror("Input Error", "Input (Path/URL/Name) is required.")
            return
        if not DOWNLOAD_FOLDER_GLOBAL:
            messagebox.showerror("Input Error", "Download folder is required.")
            return
        if not MC_VERSION_INPUT_GLOBAL:
            messagebox.showerror("Input Error", "Minecraft version is required.")
            return
        if current_mode == "Flexible Source Download" and MC_VERSION_INPUT_GLOBAL.lower() == "best":
            messagebox.showerror("Input Error", "'best' Minecraft version is not supported for 'Flexible Source Download' mode. Please specify a version.")
            return
        
        os.makedirs(DOWNLOAD_FOLDER_GLOBAL, exist_ok=True)

        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", tk.END)
        self.log_textbox.configure(state="disabled")
        self.log_message("Starting processing...")
        self.log_message(f"Mode: {current_mode}")
        self.log_message(f"MC Version Input: {MC_VERSION_INPUT_GLOBAL}")
        self.log_message(f"Loader: {LOADER_GLOBAL}")
        self.log_message(f"Download Folder: {DOWNLOAD_FOLDER_GLOBAL}")

        self.start_button.configure(state="disabled", text="Processing...")
        self.progress_bar.set(0)
        
        def threaded_task():
            try:
                if current_mode == "HTML Modlist":
                    process_modlist_from_html_backend(input_path_or_name)
                elif current_mode == "Single Mod + Dependencies (CurseForge)":
                    process_single_mod_and_dependencies_backend(input_path_or_name)
                elif current_mode == "Modrinth Collection":
                    process_modrinth_collection_backend(input_path_or_name)
                elif current_mode == "Flexible Source Download":
                    process_flexible_source_download_backend(input_path_or_name)
                self.log_message("Processing finished.")
            except Exception as e:
                self.log_message(f"An critical error occurred: {e}")
                import traceback
                self.log_message(traceback.format_exc())
            finally:
                self.after(0, self.processing_finished)

        thread = threading.Thread(target=threaded_task, daemon=True)
        thread.start()

    def processing_finished(self):
        """Called when backend processing is complete to update GUI."""
        self.start_button.configure(state="normal", text="Start Processing")
        self.progress_bar.set(1)
        self.stop_progress_indeterminate()
        self.choice_frame.grid_remove() 

        if MISSED_ITEMS_GLOBAL:
            summary_title = "Download Summary - Missed/Skipped Items"
            summary_body = "Warning: Some items had issues.\nCheck manually:\n\n"
            
            unique_missed = []
            seen_identifiers = set()
            for item in MISSED_ITEMS_GLOBAL:
                identifier = item.get("url", "") + item.get("reason", "")
                if identifier not in seen_identifiers:
                    unique_missed.append(item)
                    seen_identifiers.add(identifier)

            for item in unique_missed:
                summary_body += f"- Name/ID: {item.get('name', 'N/A')}\n  Reason: {item.get('reason', 'N/A')}\n  Source: {item.get('url', 'N/A')}\n\n"
            
            summary_window = ctk.CTkToplevel(self)
            summary_window.title(summary_title)
            summary_window.geometry("500x400")
            
            summary_textbox = ctk.CTkTextbox(summary_window, wrap="word", height=350, width=480)
            summary_textbox.pack(padx=10, pady=10, fill="both", expand=True)
            summary_textbox.insert("1.0", summary_body)
            summary_textbox.configure(state="disabled")
            
            close_button = ctk.CTkButton(summary_window, text="Close", command=summary_window.destroy)
            close_button.pack(pady=5)
            summary_window.grab_set()
        else:
            self.log_message("All items processed successfully or already existed.")
            messagebox.showinfo("Success", "All items processed successfully or already existed.")
        self.progress_bar.set(0)


if __name__ == "__main__":
    if not API_KEY or API_KEY.startswith("$2a$10$8L"):
        print("WARNING: API_KEY might be a placeholder. GUI will launch but API calls may fail.")
    app = App()
    app.mainloop()