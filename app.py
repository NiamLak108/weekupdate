import os
import re
import json
import random
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from llmproxy import generate
from duckduckgo_search import duckduckgo_search  # Function-based API

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
    results = duckduckgo_search(query, max_results=5)
    # Expecting results as a list of dicts containing "href"
    return [r["href"] for r in results]

def get_page(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, "html.parser")
        # Remove non-content tags for cleaner text
        for tag in soup(["script", "style", "header", "footer", "nav", "aside"]):
            tag.extract()
        text = soup.get_text(separator=" ", strip=True)
        return " ".join(text.split())[:1500]
    return f"Failed to fetch {url}, status code: {response.status_code}"

def youtube_search(query):
    results = duckduckgo_search(f"{query} site:youtube.com", max_results=5)
    return [r["href"] for r in results if "youtube.com/watch" in r["href"]]

def tiktok_search(query):
    results = duckduckgo_search(f"{query} site:tiktok.com", max_results=5)
    return [r["href"] for r in results if "tiktok.com" in r["href"]]

def instagram_search(query):
    hashtag = query.replace(" ", "")
    results = duckduckgo_search(f"#{hashtag} site:instagram.com", max_results=5)
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
    Create the system message based on user and health info, then call the LLM agent.
    The agent returns a tool call (e.g., youtube_search("gut health smoothies")).
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

# --- WEEKLY UPDATE HELPER FUNCTION ---
def weekly_update_internal(user):
    """
    Helper function to generate the weekly update for a given user.
    Returns a dictionary with the update results.
    """
    if user not in session_dict:
        return {"text": "User not found in session."}
    
    user_session = session_dict[user]
    user_info = {
        "name": user,
        "news_sources": user_session.get("news_sources", ["bbc.com", "nytimes.com"]),
        "news_pref": user_session.get("news_pref", "Research News")
    }
    health_info = {
        "condition": user_session.get("condition", "unknown condition")
    }
    
    try:
        agent_response = agent_weekly_update(user_info, health_info)
        print(f"✅ Final agent response: {agent_response}")

        # Extract tool call from the agent response
        tool_call = extract_tool(agent_response)

        # Fallback if no valid tool call is provided
        if not tool_call:
            print("⚠️ No valid tool call found. Using fallback.")
            condition = health_info.get("condition")
            pref = user_info.get("news_pref", "Research News").lower()
            tool_map = {
                'youtube': f'youtube_search("{condition} tips")',
                'tiktok': f'tiktok_search("{condition} tips")',
                'instagram reel': f'instagram_search("{condition} tips")',
                'research news': f'websearch("{condition} tips")'
            }
            key = pref if pref in tool_map else "research news"
            tool_call = tool_map.get(key)

        print(f"🔁 Final tool to execute: {tool_call}")

        # Execute the tool call (note: using eval has security implications; ensure inputs are trusted)
        results = eval(tool_call)
        output = "\n".join(f"• {item}" for item in results)
        return {
            "agent_response": agent_response,
            "executed_tool": tool_call,
            "results": output
        }
    except Exception as e:
        import traceback
        print("❌ Exception during weekly update:")
        traceback.print_exc()
        return {"error": str(e)}

# --- WEEKLY UPDATE ROUTE (GET) ---
@app.route('/weekly_update', methods=['GET'])
def weekly_update_get():
    """
    GET endpoint for weekly update. (You can still use this if needed.)
    """
    user_name = request.args.get("user", "default_user")
    if user_name not in session_dict:
        return jsonify({"error": "User not found in session store."}), 404
    return jsonify(weekly_update_internal(user_name))

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

    # If the user requests "restart", reinitialize the session.
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

    # If user types "weekly update", trigger the weekly update and return its result.
    if message.lower() == "weekly update":
        # Ensure user has completed onboarding
        if session_dict[user].get("onboarding_stage") == "done":
            update_response = weekly_update_internal(user)
            return jsonify(update_response)
        else:
            return jsonify({"text": "Please complete onboarding before requesting a weekly update."})
    
    # During onboarding, use the first_interaction flow.
    if session_dict[user]["onboarding_stage"] != "done":
        response = first_interaction(message, user)
    else:
        # If fully onboarded but message is not a weekly update command, you can prompt:
        response = {"text": "You're fully onboarded. Type 'weekly update' to get your update."}

    save_sessions(session_dict)
    return jsonify(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)



