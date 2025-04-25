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

# Load or initialize the sessions dict
session_dict = load_sessions()

# --- DUMMY TEST USER (skip onboarding) ---
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
    with DDGS() as ddgs:
        results = ddgs.text(query, max_results=5)
    return [r.get("href") for r in results]


def youtube_search(query):
    with DDGS() as ddgs:
        results = ddgs.text(f"{query} site:youtube.com", max_results=5)
    return [r.get("href") for r in results if "youtube.com/watch" in r.get("href", "")]


def tiktok_search(query):
    with DDGS() as ddgs:
        results = ddgs.text(f"{query} site:tiktok.com", max_results=5)
    return [r.get("href") for r in results if "tiktok.com" in r.get("href", "")]


def instagram_search(query):
    with DDGS() as ddgs:
        results = ddgs.text(f"{query} site:instagram.com", max_results=10)
    links = []
    for r in results:
        url = r.get("href", "")
        if url and "instagram.com" in url:
            links.append(url)
        if len(links) >= 5:
            break
    return links

# --- WEEKLY UPDATE GENERATION ---
# Map user-visible channel to (function_name, function)
TOOL_MAP = {
    "YouTube": ("youtube_search", youtube_search),
    "TikTok": ("tiktok_search", tiktok_search),
    "Instagram Reel": ("instagram_search", instagram_search),
    "Research News": ("websearch", websearch)
}

def agent_weekly_update(func_name, condition):
    # Generate exactly three unique calls for the specified tool and condition
    prompt = (
        f"Generate exactly three unique calls using only {func_name}."
        f" Each call should look like: {func_name}(\"{condition} ...\")."
        f" The search phrase must include '{condition}'. Return one call per line."
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
    condition = sess.get("condition")
    func_name, func = TOOL_MAP.get(pref, ("websearch", websearch))

    # Step 1: generate raw calls
    raw = agent_weekly_update(func_name, condition)
    lines = [line.strip() for line in raw.splitlines() if line.strip().startswith(f"{func_name}(")]
    seen = []
    for l in lines:
        if l not in seen:
            seen.append(l)
    calls = seen[:3]

    # Step 2: execute and collect top links
    results = []
    for call in calls:
        m = re.match(rf"{func_name}\(\"(.+)\"\)", call)
        if m:
            query_str = m.group(1)
            print("Querying:", func_name, query_str)
            print("Raw links:", func(query_str))
            try:
                links = func(query_str)
                top = links[0] if links else "No results found"
            except Exception:
                top = "Error fetching results"
        else:
            top = "Invalid call"
        results.append({"query": call, "link": top})

    # Ensure exactly three entries (pad if necessary)
    while len(results) < 3:
        results.append({"query": f"{func_name}(\"{condition}\")", "link": "No call generated"})

    # Format the response
    text_lines = [f"• {r['query']}: {r['link']}" for r in results]
    return {"text": "Here is your weekly health content digest with 3 unique searches:\n" + "\n".join(text_lines),
            "results": results}

# --- ONBOARDING FUNCTIONS ---
def first_interaction(message, user):
    # Existing onboarding logic should go here
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










