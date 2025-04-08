import os
import re
import json
import random
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from llmproxy import generate
from duckduckgo_search import DuckDuckGoSearch

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
    ddg = DuckDuckGoSearch()
    results = ddg.text(query, max_results=5)
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
    ddg = DuckDuckGoSearch()
    results = ddg.text(f"{query} site:youtube.com", max_results=5)
    return [r["href"] for r in results if "youtube.com/watch" in r["href"]]

def tiktok_search(query):
    ddg = DuckDuckGoSearch()
    results = ddg.text(f"{query} site:tiktok.com", max_results=5)
    return [r["href"] for r in results if "tiktok.com" in r["href"]]


def instagram_search(query):
    hashtag = query.replace(" ", "")
    ddg = DuckDuckGoSearch()
    results = ddg.text(f"#{hashtag} site:instagram.com", max_results=5)
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
    Create the system message using the session values and call the LLM agent.
    user_info is a dict containing keys like 'name', 'preferred_platform', and 'news_pref'.
    health_info is a dict containing health-related info (e.g. condition).
    """
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

ONLY respond with one tool call. Do NOT explain or add any extra text.
Make your query specific, relevant to the condition, and useful.

Each time you search, make sure the search query is different from the previous week's content.
"""
    response = generate(
        model='4o-mini',
        system=system,
        query="What should I send this user this week?",
        temperature=0.9,
        lastk=30,
        session_id='HEALTH_UPDATE_AGENT',
        rag_usage=False
    )
    print(f"🔍 Raw agent response: {response}")
    return response['response']

# --- WEEKLY UPDATE ROUTE ---
@app.route('/weekly_update', methods=['GET'])
def weekly_update():
    """
    Endpoint to trigger the weekly update. This uses session data to
    generate the personalized content, runs the selected search tool,
    and returns the result.
    """
    # For demo purposes, we simply use a pre-defined user key.
    # In production you would likely authenticate and use the current user's session.
    user_name = request.args.get("user", "default_user")
    if user_name not in session_dict:
        return jsonify({"error": "User not found in session store."}), 404

    user_session = session_dict[user_name]
    
    # Prepare user information for the weekly update
    user_info = {
        "name": user_name,
        "news_sources": user_session.get("news_sources", ["bbc.com", "nytimes.com"]),
        # Here, we use the 'news_pref' field from onboarding – ensure it matches the tool names
        "news_pref": user_session.get("news_pref", "Research News")
    }
    # Health profile information (e.g., condition) coming from session data; you may need to adjust key names.
    health_info = {
        "condition": user_session.get("condition", "unknown condition")
    }
    
    try:
        # Get the agent response containing a tool call.
        agent_response = agent_weekly_update(user_info, health_info)
        print(f"✅ Final agent response: {agent_response}")

        # Extract the tool call, for example: youtube_search("crohn's anti-inflammatory meals")
        tool_call = extract_tool(agent_response)

        # If no valid tool call is provided, use a fallback using minimal logic.
        if not tool_call:
            print("⚠️ No valid tool call found. Using fallback.")
            condition = health_info.get("condition")
            # Map onboarding news preference to a tool function name.
            pref = user_info.get("news_pref", "Research News").lower()
            tool_map = {
                'youtube': f'youtube_search("{condition} tips")',
                'tiktok': f'tiktok_search("{condition} tips")',
                'instagram reel': f'instagram_search("{condition} tips")',
                'research news': f'websearch("{condition} tips")'
            }
            # Normalize key lookup.
            key = pref if pref in tool_map else "research news"
            tool_call = tool_map.get(key)

        print(f"🔁 Final tool to execute: {tool_call}")

        # Execute the tool call. WARNING: Using eval can be insecure! Be sure to validate or sandbox inputs.
        results = eval(tool_call)
        output = "\n".join(f"• {item}" for item in results)
        return jsonify({
            "agent_response": agent_response,
            "executed_tool": tool_call,
            "results": output
        })
    except Exception as e:
        import traceback
        print("❌ Exception during weekly update:")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# --- ONBOARDING FUNCTIONS FOR CHATBOT ---
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
        # Sample buttons for response options
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
@app.route('/query', methods=['POST'])
def main():
    global session_dict
    data = request.get_json()
    message = data.get("text", "").strip()
    user = data.get("user_name", "Unknown")

    # Reload sessions from file
    session_dict = load_sessions()
    print("Current session:", session_dict.get(user, {}))
    print("User:", user)

    # Restart handling if requested
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

    # Initialize user session if not present
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

    # During onboarding, use the first_interaction flow; otherwise, use the daily update.
    if session_dict[user]["onboarding_stage"] != "done":
        response = first_interaction(message, user)
    else:
        response = {"text": "You're fully onboarded. To trigger your weekly update, visit /weekly_update?user=" + user}

    save_sessions(session_dict)
    return jsonify(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
