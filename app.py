import os
import time
import json
from flask import Flask, request, jsonify
from llmproxy import generate
from duckduckgo_search import DDGS

app = Flask(__name__)

# --- SESSION MANAGEMENT ---
SESSION_FILE = "session_store.json"

def load_sessions():
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_sessions(sessions):
    with open(SESSION_FILE, "w") as f:
        json.dump(sessions, f, indent=4)

session_dict = load_sessions()

def _init_test_user():
    session_dict.setdefault("test_user", {
        "session_id": "test_user-session",
        "onboarding_stage": "done",
        "condition": "Crohn's disease",
        "news_pref": None,
        "news_sources": ["bbc.com", "nytimes.com"]
    })
    save_sessions(session_dict)

_init_test_user()

# --- PERSISTENT DDGS SESSION & RATE-LIMIT HELPERS ---
ddgs = DDGS()

def ddgs_search(query, max_results=20, retries=3):
    """
    Wrapper around ddgs.text that retries with exponential backoff on rate-limit.
    """
    for attempt in range(retries):
        try:
            return ddgs.text(query, max_results=max_results)
        except Exception as e:
            if "Ratelimit" in str(e):
                time.sleep(2 ** attempt)
                continue
            else:
                raise
    return []

# --- TOOL FUNCTIONS ---
def websearch(query):
    """
    Perform a DuckDuckGo text search and return up to 5 external URLs.
    """
    results = ddgs_search(query, max_results=20)
    links = []
    for r in results:
        url = r.get("href") or r.get("url")
        if not url or "duckduckgo.com" in url:
            continue
        links.append(url)
        if len(links) >= 5:
            break
    return links

def youtube_search(query):
    """
    Only fetch actual YouTube video URLs matching the query.
    """
    ddg_query = f'site:youtube.com/watch "{query}"'
    results = ddgs_search(ddg_query, max_results=30)
    links = []
    for r in results:
        url = r.get("href") or r.get("url")
        if url and ("youtube.com/watch" in url or "youtu.be/" in url):
            links.append(url)
        if len(links) >= 5:
            break
    return links

def tiktok_search(query):
    """
    Only fetch TikTok video URLs matching the query.
    """
    ddg_query = f'site:tiktok.com/video "{query}"'
    results = ddgs_search(ddg_query, max_results=30)
    links = []
    for r in results:
        url = r.get("href") or r.get("url")
        if url and "/video/" in url and "tiktok.com" in url:
            links.append(url)
        if len(links) >= 5:
            break
    return links

def instagram_search(query):
    """
    Only fetch Instagram Reel or post URLs matching the query.
    """
    ddg_query = f'site:instagram.com/reel "{query}"'
    results = ddgs_search(ddg_query, max_results=30)
    links = []
    for r in results:
        url = r.get("href") or r.get("url")
        if url and "instagram.com" in url and ("/reel/" in url or "/p/" in url):
            links.append(url)
        if len(links) >= 5:
            break
    return links

# --- WEEKLY UPDATE GENERATION ---
TOOL_MAP = {
    "YouTube": ("youtube_search", youtube_search),
    "TikTok": ("tiktok_search", tiktok_search),
    "Instagram Reel": ("instagram_search", instagram_search),
    "Research News": ("websearch", websearch)
}
PRIMARIES_WITH_FALLBACK = {"youtube_search", "tiktok_search", "instagram_search"}

def agent_weekly_update(func_name, condition):
    prompt = (
        f"Generate exactly three unique search phrases including '{condition}' using only {func_name}."
        " Return one phrase per line, no code syntax."
    )
    resp = generate(
        model="4o-mini",
        system=prompt,
        query=prompt,
        temperature=0.7,
        lastk=30,
        session_id="HEALTH_UPDATE_AGENT",
        rag_usage=False
    )
    return resp.get("response", "")

def weekly_update_internal(user):
    sess = session_dict.get(user)
    if not sess:
        return {"text": "User not found."}

    pref = sess.get("news_pref")
    condition = sess.get("condition") or session_dict["test_user"]["condition"]
    func_name, func = TOOL_MAP.get(pref, ("websearch", websearch))

    raw = agent_weekly_update(func_name, condition)
    queries = []
    for line in raw.splitlines():
        phrase = line.strip().strip('"')
        if phrase and phrase not in queries:
            queries.append(phrase)
    queries = queries[:3]

    results = []
    for q in queries:
        try:
            links = func(q)
            if not links and func_name in PRIMARIES_WITH_FALLBACK:
                domain = func_name.replace('_search', '') + ".com"
                links = websearch(f"{q} site:{domain}")
            top = links[0] if links else "No results found"
        except Exception as e:
            top = f"Error fetching results: {e}"
        results.append({"query": q, "link": top})

    while len(results) < 3:
        results.append({"query": condition, "link": "No call generated"})

    text = "Here is your weekly health content digest with 3 unique searches:\n"
    text += "\n".join(f"• {r['query']}: {r['link']}" for r in results)
    return {"text": text, "results": results}

# --- ONBOARDING & MAIN ROUTE ---
def first_interaction(message, user):
    return {"text": "..."}

@app.route('/', methods=['POST'])
def main():
    global session_dict
    data = request.get_json()
    message = data.get("text", "").strip()
    user = data.get("user_name", "Unknown")

    session_dict = load_sessions()
    if user not in session_dict:
        session_dict[user] = {
            "session_id": f"{user}-session",
            "onboarding_stage": "condition",
            "condition": "",
            "age": 0,
            "weight": 0,
            "medications": [],
            "emergency_contact": "",
            "news_pref": "",
            "news_sources": ["bbc.com", "nytimes.com"]
        }
        save_sessions(session_dict)

    if message.lower() == "weekly update":
        buttons = [
            {"type": "button", "text": "🎥 YouTube",        "msg": "YouTube",        "msg_in_chat_window": True},
            {"type": "button", "text": "📸 Instagram Reel", "msg": "Instagram Reel", "msg_in_chat_window": True},
            {"type": "button", "text": "🎵 TikTok",         "msg": "TikTok",         "msg_in_chat_window": True},
            {"type": "button", "text": "🧪 Research News",  "msg": "Research News",  "msg_in_chat_window": True}
        ]
        return jsonify({
            "text": "Choose your weekly-update content type:",
            "attachments": [{"collapsed": False, "color": "#e3e3e3", "actions": buttons}]
        })

    if message in TOOL_MAP:
        session_dict[user]["news_pref"] = message
        session_dict[user]["onboarding_stage"] = "done"
        save_sessions(session_dict)
        return jsonify(weekly_update_internal(user))

    if session_dict[user].get("onboarding_stage") != "done":
        response = first_interaction(message, user)
    else:
        response = {"text": "You're onboarded! Type 'weekly update' to choose content and get your digest."}

    save_sessions(session_dict)
    return jsonify(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)







