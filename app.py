import os
import re
import json
import random
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from llmproxy import generate
from duckduckgo_search import DDGS  # Use the DDGS class for searches

app = Flask(__name__)

# --- SESSION MANAGEMENT ---
SESSION_FILE = "session_store.json"

def load_sessions():
    """Load stored sessions from a JSON file."""
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r") as file:
            try:
                return json.load(file)
            except json.JSONDecodeError:
                return {}
    return {}

def save_sessions(session_dict):
    """Save sessions to a JSON file."""
    with open(SESSION_FILE, "w") as file:
        json.dump(session_dict, file, indent=4)

# Global sessions used by the app.
session_dict = load_sessions()

# --- TOOL FUNCTIONS ---
def websearch(query):
    with DDGS() as ddgs:
        results = ddgs.text(query, max_results=5)
    return [r["href"] for r in results]

def get_page(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, "html.parser")
        # Remove non-content tags for a cleaner text
        for tag in soup(["script", "style", "header", "footer", "nav", "aside"]):
            tag.extract()
        text = soup.get_text(separator=" ", strip=True)
        return " ".join(text.split())[:1500]
    return f"Failed to fetch {url}, status code: {response.status_code}"

def youtube_search(query):
    with DDGS() as ddgs:
        results = ddgs.text(f"{query} site:youtube.com", max_results=5)
    return [r["href"] for r in results if "youtube.com/watch" in r["href"]]

def tiktok_search(query):
    with DDGS() as ddgs:
        results = ddgs.text(f"{query} site:tiktok.com", max_results=5)
    return [r["href"] for r in results if "tiktok.com" in r["href"]]

def instagram_search(query):
    hashtag = query.replace(" ", "")
    with DDGS() as ddgs:
        results = ddgs.text(f"#{hashtag} site:instagram.com", max_results=5)
    return [r["href"] for r in results if "instagram.com" in r["href"]]

# --- TOOL PARSER ---
def extract_tool(text):
    for tool in ["websearch", "get_page", "youtube_search", "tiktok_search", "instagram_search"]:
        match = re.search(fr'{tool}\([^)]*\)', text)
        if match:
            return match.group()
    return None

# --- WEEKLY UPDATE FUNCTION ---
def agent_weekly_update(user_info, health_info):
    """
    Generate five unique content search queries for this week's update using the original system prompt,
    then return exactly five distinct tool calls.
    """
    # Preserve original in-depth system prompt
    system = f"""
You are an AI agent designed to handle weekly health content updates for users with specific health conditions.

In addition to your own intelligence, you are given access to a set of tools that let you fetch personalized health content from various online platforms.

Your job is to use the right tool to deliver a helpful and engaging content recommendation **based on the user's health condition and preferences**.

Think step-by-step about which platform is best for this week's update, and then return the correct tool call using the examples provided.

ONLY respond with a tool call like: youtube_search("gut health smoothies")

### USER INFORMATION ###
- Name: {user_info.get('name')}
- Health condition: {health_info.get('condition')}
- Preferred platform: {user_info.get('news_pref')}
- Preferred news sources: {", ".join(user_info.get('news_sources', []))}

### PROVIDED TOOLS INFORMATION ###

##1. Tool to perform a YouTube video search
Name: youtube_search
Parameters: query
Example usage: youtube_search("crohn's anti-inflammatory meals")

##2. Tool to search TikTok for short-form video content
Name: tiktok_search
Parameters: query
Example usage: tiktok_search("what I eat with IBS")

##3. Tool to search Instagram posts/reels via hashtags
Name: instagram_search
Parameters: query
Example usage: instagram_search("gut healing routine")

##4. Tool to perform a websearch using DuckDuckGo
Name: websearch
Parameters: query
Example usage: websearch("best probiotics for gut health site:bbc.com")
Example usage: websearch("latest Crohn's breakthroughs site:nytimes.com")

ONLY respond with five unique tool calls, one per line, and do NOT add any extra text.
Make your queries each specifically tailored to the condition, varied, and distinct from each other.
"""
    response = generate(
        model='4o-mini',
        system=system,
        query="Generate five unique tool calls, one per line.",
        temperature=0.9,
        lastk=30,
        session_id='HEALTH_UPDATE_AGENT',
        rag_usage=False
    )
    return response['response']


# --- WEEKLY UPDATE INTERNAL HELPER ---
def weekly_update_internal(user):
    """
    Generate the weekly update for a given user with 5 unique queries and return their top link.
    """
    if user not in session_dict:
        return {"text": "User not found in session."}

    user_session = session_dict[user]
    user_info = {
        "name": user,
        "news_sources": user_session.get("news_sources", []),
        "news_pref": user_session.get("news_pref", "Research News")
    }
    health_info = {"condition": user_session.get("condition", "unknown condition")}

    # Step 1: Fetch initial batch of calls
    raw = agent_weekly_update(user_info, health_info)
    calls = re.findall(r'(youtube_search\("[^"]+"\)|tiktok_search\("[^"]+"\)|instagram_search\("[^"]+"\)|websearch\("[^"]+"\))', raw)

    # Step 2: If fewer than 5 unique, iteratively request more distinct calls
    seen = set(calls)
    while len(calls) < 5:
        prompt = f"Generate one additional unique tool call distinct from these: {', '.join(seen)}"
        extra = generate(
            model='4o-mini',
            system=system,
            query=prompt,
            temperature=0.9,
            lastk=30,
            session_id='HEALTH_UPDATE_AGENT',
            rag_usage=False
        )['response']
        match = re.search(r'(youtube_search\("[^"]+"\)|tiktok_search\("[^"]+"\)|instagram_search\("[^"]+"\)|websearch\("[^"]+"\))', extra)
        if match:
            call = match.group(0)
            if call not in seen:
                seen.add(call)
                calls.append(call)
                continue
        # Break if no new unique call
        break

    # Step 3: Execute and collect top links
    results = []
    for call in calls[:5]:
        try:
            links = eval(call)
            top = links[0] if links else 'No results found'
        except Exception:
            top = 'Error executing query'
        results.append({"query": call, "link": top})

    # Step 4: Format response
    lines = [f"• {r['query']}: {r['link']}" for r in results]
    text = "Here is your weekly health content digest with 5 unique searches:\n" + "\n".join(lines)

    return {"text": text, "queries": calls[:5], "results": results}



# --- ONBOARDING FUNCTIONS ---
def first_interaction(message, user):
    questions = {
        "condition": "🏪 What condition do you have? (Type II Diabetes, Crohn’s disease, or both)",
        "age": "👋 Hi, I'm DocBot — your health assistant!\n"
               "I'll help you track symptoms, remind you about meds 💊, and send you health tips 📰.\n\n"
               "Let’s start with a few quick questions.\n 🎂 How old are you?",
        "weight": "⚖️ What's your weight (in kg)?",
        "medications": "💊 What medications are you currently taking?",
        "emergency_contact": "📱 Who should we contact in case of emergency? [email]",
        "news_pref": "📰 What kind of weekly health updates would you like?\nOptions: Instagram Reel 📱, TikTok 🎵, or Research News 🧪"
    }

    stage = session_dict[user].get("onboarding_stage", "condition")

    if stage == "condition":
        session_dict[user]["condition"] = message
        session_dict[user]["onboarding_stage"] = "age"
        return {"text": questions["age"]}
    elif stage == "age":
        if not message.isdigit():
            return {"text": "❗ Please enter a valid age (a number)."}
        session_dict[user]["age"] = int(message)
        session_dict[user]["onboarding_stage"] = "weight"
        return {"text": questions["weight"]}
    elif stage == "weight":
        session_dict[user]["weight"] = message
        session_dict[user]["onboarding_stage"] = "medications"
        return {"text": questions["medications"]}
    elif stage == "medications":
        session_dict[user]["medications"] = [med.strip() for med in message.split(",")]
        session_dict[user]["onboarding_stage"] = "emergency_contact"
        return {"text": questions["emergency_contact"]}
    elif stage == "emergency_contact":
        session_dict[user]["emergency_contact"] = message
        session_dict[user]["onboarding_stage"] = "news_pref"
        buttons = [
            {"type": "button", "text": "🎥 YouTube", "msg": "YouTube", "msg_in_chat_window": True, "button_id": "youtube_button"},
            {"type": "button", "text": "📸 IG Reel", "msg": "Instagram Reel", "msg_in_chat_window": True, "button_id": "insta_button"},
            {"type": "button", "text": "🎵 TikTok", "msg": "TikTok", "msg_in_chat_window": True, "button_id": "tiktok_button"},
            {"type": "button", "text": "🧪 Research", "msg": "Research News", "msg_in_chat_window": True, "button_id": "research_button"}
        ]
        return {
            "text": "📰 What kind of weekly health updates would you like?",
            "attachments": [{"collapsed": False, "color": "#e3e3e3", "actions": buttons}]
        }
    elif stage == "news_pref":
        valid_options = ["YouTube", "Instagram Reel", "TikTok", "Research News"]
        if message not in valid_options:
            return {"text": "Please click one of the buttons above to continue."}
        session_dict[user]["news_pref"] = message
        session_dict[user]["onboarding_stage"] = "condition1"
        buttons = [
            {"type": "button", "text": "Crohn's", "msg": "Crohn's", "msg_in_chat_window": True, "button_id": "choose_condition_crohns"},
            {"type": "button", "text": "Type II Diabetes", "msg": "Type II Diabetes", "msg_in_chat_window": True, "button_id": "choose_condition_diabetes"}
        ]
        return {
            "text": "🏪 What condition do you have?",
            "attachments": [{"collapsed": False, "color": "#e3e3e3", "actions": buttons}]
        }
    elif stage == "condition1":
        valid_conditions = ["Crohn's", "Type II Diabetes"]
        if message not in valid_conditions:
            return {"text": "Please click one of the buttons above to continue."}
        session_dict[user]["condition"] = message
        session_dict[user]["onboarding_stage"] = "done"
        return {"text": "📆 Onboarding complete! You can now access daily and weekly updates."}

# --- MAIN CHAT ROUTE ---
@app.route('/', methods=['POST'])
def main():
    global session_dict
    data = request.get_json()
    message = data.get("text", "").strip()
    user = data.get("user_name", "Unknown")

    # Reload sessions from file
    session_dict = load_sessions()
    print("Current session:", session_dict.get(user, {}))
    print("User:", user)

    # If the user sends "restart", reinitialize onboarding
    if "restart" in message.lower():
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
        response = first_interaction("", user)
        return jsonify({"text": "🔄 Restarted onboarding. " + response.get("text", "")})

    # Initialize session if user is new
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

    # If user types "weekly update", trigger the update and return results in chat
    if message.lower() == "weekly update":
        if session_dict[user].get("onboarding_stage") == "done":
            update_response = weekly_update_internal(user)
            return jsonify(update_response)
        else:
            return jsonify({"text": "Please complete onboarding before requesting a weekly update."})

    # Use the onboarding flow if not finished
    if session_dict[user]["onboarding_stage"] != "done":
        response = first_interaction(message, user)
    else:
        response = {"text": "You're fully onboarded. Type 'weekly update' to get your update."}

    save_sessions(session_dict)
    return jsonify(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)












