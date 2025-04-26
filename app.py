import os
import re
import json
import requests
from bs4 import BeautifulSoup
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

# Load sessions
session_dict = load_sessions()

# --- DUMMY TEST USER ---
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

# --- TOOL FUNCTIONS ---
def websearch(query):
    """
    Perform a DuckDuckGo text search and return up to 5 external URLs.
    """
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=10)
    except Exception:
        return []
    links = []
    for r in results:
        url = r.get("href")
        if not url or "duckduckgo.com" in url:
            continue
        links.append(url)
        if len(links) >= 5:
            break
    return links


def youtube_search(query):
    """
    Search YouTube via DuckDuckGo and return up to 5 video URLs.
    """
    try:
        with DDGS() as ddgs:
            results = ddgs.text(f"{query} site:youtube.com", max_results=10)
    except Exception:
        return []
    links = []
    for r in results:
        url = r.get("href")
        if not url or "duckduckgo.com" in url:
            continue
        if "youtube.com/watch" in url:
            links.append(url)
        if len(links) >= 5:
            break
    return links


def tiktok_search(query):
    """
    Search TikTok via DuckDuckGo and return up to 5 URLs.
    """
    try:
        with DDGS() as ddgs:
            results = ddgs.text(f"{query} site:tiktok.com", max_results=10)
    except Exception:
        return []
    links = []
    for r in results:
        url = r.get("href")
        if not url or "duckduckgo.com" in url:
            continue
        if "tiktok.com" in url and "watch" in url:
            links.append(url)
        elif "tiktok.com" in url:
            links.append(url)
        if len(links) >= 5:
            break
    return links


def instagram_search(query):
    """
    Search Instagram via DuckDuckGo and return up to 5 URLs.
    """
    try:
        with DDGS() as ddgs:
            results = ddgs.text(f"{query} site:instagram.com", max_results=15)
    except Exception:
        return []
    links = []
    for r in results:
        url = r.get("href")
        if not url or "duckduckgo.com" in url:
            continue
        if "instagram.com" in url:
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

def agent_weekly_update(func_name, condition):
    prompt = (
        f"Generate exactly three unique search phrases including '{condition}' using only {func_name}."
        f" Return one phrase per line without any code syntax." 
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

    # Determine preference and condition
    pref = sess.get("news_pref")
    condition = sess.get("condition") or session_dict.get("test_user", {}).get("condition", "")
    func_name, func = TOOL_MAP.get(pref, ("websearch", websearch))

    # Generate raw phrases
    raw = agent_weekly_update(func_name, condition)

    # Extract bare phrases
    queries = []
    for line in raw.splitlines():
        phrase = line.strip().strip('"')
        if phrase:
            queries.append(phrase)
    # Dedupe & limit
    seen = []
    for q in queries:
        if q not in seen:
            seen.append(q)
    queries = seen[:3]

    # Execute and collect top links
    results = []
    for q in queries:
        try:
            links = func(q)
            top = links[0] if links else "No results found"
        except Exception as e:
            top = f"Error fetching results: {e}"
        results.append({"query": q, "link": top})

    # Pad to three
    while len(results) < 3:
        results.append({"query": condition, "link": "No call generated"})

    # Format output
    lines = [f"• {r['query']}: {r['link']}" for r in results]
    return {"text": "Here is your weekly health content digest with 3 unique searches:\n" + "\n".join(lines),
            "results": results}

# --- ONBOARDING (unchanged) ---
def first_interaction(message, user):
    return {"text": "..."}

# --- MAIN ROUTE ---
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
            {"type": "button", "text": "🎥 YouTube", "msg": "YouTube", "msg_in_chat_window": True},
            {"type": "button", "text": "📸 Instagram Reel", "msg": "Instagram Reel", "msg_in_chat_window": True},
            {"type": "button", "text": "🎵 TikTok", "msg": "TikTok", "msg_in_chat_window": True},
            {"type": "button", "text": "🧪 Research News", "msg": "Research News", "msg_in_chat_window": True}
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












