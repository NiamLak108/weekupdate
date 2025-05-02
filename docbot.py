from session_mgmt import *
from user_tools import *
from flask import send_file
import threading
import schedule
import time
import os
import json
from flask import Flask, request, jsonify, Response
from llmproxy import *
import re
from duckduckgo_search import DDGS 
from bs4 import BeautifulSoup
import requests
import random
from instr import daily_system_template, general_system_template


app = Flask(__name__)


# --- WEEKLY UPDATE FUNCTION ---
def agent_weekly_update(user_info, health_info):
    """
    Create a system message using the user and health info, then call the LLM agent.
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
        lastk=10,
        session_id='HEALTH_UPDATE_AGENT',
        rag_usage=False
    )
    print(f"🔍 Raw agent response: {response}")
    return response['response']



# --- WEEKLY UPDATE INTERNAL HELPER ---
def weekly_update_internal(message, user, session_dict):
    """
    Generate the weekly update for a given user.
    Returns a dictionary with the update results including a "text" key for display.
    """
    valid_options = ["YouTube", "Instagram Reel", "TikTok", "Research News"]
    if message == "Generate my weekly update":
        buttons = [
            {"type": "button", "text": "🎥 YouTube", "msg": "YouTube", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "youtube_button"},
            {"type": "button", "text": "📸 IG Reel", "msg": "Instagram Reel", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "insta_button"},
            {"type": "button", "text": "🎵 TikTok", "msg": "TikTok", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "tiktok_button"},
            {"type": "button", "text": "🧪 Research", "msg": "Research News", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "research_button"}
        ]

        if session_dict[user]["news_pref"] == "":
            response_text = "📰 Welcome to the weekly update feature!\nEvery week, we'll send you weekly health updates that we think you'll find interesting. What format of content would you prefer?"
        elif session_dict[user]["news_pref"] == "reset":
            response_text = "📰 Let's generate your weekly update!\nWhat format of content would you prefer?"
        
        return {
            "text": response_text,
            "attachments": [{"collapsed": False, "color": "#e3e3e3", "actions": buttons}]
        }

    elif (session_dict[user]["news_pref"] == "" or session_dict[user]["news_pref"] == "reset") and message not in valid_options:
        return {"text": "Please click one of the buttons above to continue."}

    elif (session_dict[user]["news_pref"] == "" or session_dict[user]["news_pref"] == "reset") and  message in valid_options:
        session_dict[user]["news_pref"] = message
        save_sessions(session_dict)
    
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

        tool_call = extract_tool(agent_response)

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
        results = eval(tool_call)
        output = "\n".join(f"• {item}" for item in results)
        text_response = f"Here is your weekly health content digest\n{tool_call}:\n{output}"
        
        # Reset news preference for next time
        session_dict[user]["news_pref"] = "reset"
        save_sessions(session_dict)

        buttons = [
            {"type": "button", "text": "Daily wellness 📝", "msg": "Begin my daily wellness check for today", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Daily wellness check"},
            {"type": "button", "text": "Weekly update 🗞️", "msg": "Generate my weekly update", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Weekly update"},
            {"type": "button", "text": "General question 💬", "msg": "I have a general question", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "General question"}
        ]
        
        return {
            "text": text_response + "\n\n🏠 Return to main menu:",
            "agent_response": agent_response,
            "executed_tool": tool_call,
            "results": output,
            "attachments": [{"collapsed": False,"color": "#e3e3e3", "actions": buttons}]
        }
    
    except Exception as e:
        import traceback
        print("❌ Exception during weekly update:")
        traceback.print_exc()
        return {"text": f"Error: {str(e)}"}



### --- ONBOARDING FUNCTION --- ###
def first_interaction(message, user, session_dict):
    print("In first interaction")
    print(f"user condition is: {session_dict[user]['condition']}")

    questions = {
        "age": "👋 Hey there! I'm DocBot — your personalized health assistant, here to support people managing Type 2 Diabetes and Crohn's Disease. I'm here to help you stay on top of your health — from tracking symptoms and sending med reminders 💊 to sharing useful tips.\n\n"
        "Since it's your first time chatting with me, let's start with a quick intro questionnaire so I can get to know you better.\n"
        "If you need to edit an answer at any point, please say 'Restart'.\n\n"
        "🎂 First things first — how old are you? (Please enter ONLY a number)",
        "weight": "⚖️ What's your weight (in kg)?",
        "condition": "🏪 What condition do you have? Please click one of the buttons below (Crohn's disease or Type 2 Diabetes)",
        "medications": f"💊 What medications are you currently taking? Please separate each medication with a comma!",
        "doc_name": "👩‍⚕️ What is your doctor's name? Please enter as Last Name, First Name.",
        "emergency_email": "📱 For emergency contact purposes, what is your doctor's email?"
    }

    stage = session_dict[user].get("stage", "start")

    if stage == "start":
        session_dict[user]["stage"] = "age"
        save_sessions(session_dict)
        return {"text": questions["age"]}

    elif stage == "age":
        if not message.isdigit():
            return {"text": "❗ Please enter a valid age (a number)"}
        session_dict[user]["age"] = int(message)
        session_dict[user]["stage"] = "condition"
        save_sessions(session_dict)

        buttons = [
            {"type": "button", "text": "Crohn's", "msg": "Crohn's", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "choose_condition_crohns"},
            {"type": "button", "text": "Type 2 Diabetes", "msg": "Type 2 Diabetes", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "choose_condition_diabetes"}
        ]
        return {
            "text": questions["condition"],
            "attachments": [{"collapsed": False, "color": "#e3e3e3", "actions": buttons}]
        }

    # elif stage == "weight":
    #     cleaned = message.lower().replace("kg", "").strip()

    #     if not cleaned.replace('.', '', 1).isdigit():
    #         return {"text": "❗ Please enter a valid weight (a number in kg)."}
        
    #     session_dict[user]["weight"] = cleaned
    #     session_dict[user]["stage"] = "condition"
    #     save_sessions(session_dict)


    elif stage == "condition":
        valid_conditions = ["Crohn's", "Type 2 Diabetes"]

        if message not in valid_conditions:
            return {"text": "Please click one of the buttons above to continue."}

        session_dict[user]["condition"] = message
        session_dict[user]["stage"] = "medications"
        save_sessions(session_dict)

        med_examples = ""
        if session_dict[user]["condition"] == "Crohn's":
            med_examples = "aminosalicylates, corticosteroids, immunomodulators"
        elif session_dict[user]["condition"] == "Type 2 Diabetes":
            med_examples = "metformin, sulfonylureas, insulin"

        return {"text": questions["medications"] + f" (e.g. {med_examples})"}

    elif stage == "medications":
        # if len(message.split()) > 1 and "," not in message:
        #     return {"text": "❗ Please separate all medications with commas"}
        session_dict[user]["medications"] = [med.strip() for med in message.split(",")]
        session_dict[user]["stage"] = "doc_name"
        save_sessions(session_dict)
        return {"text": questions["doc_name"]}
    
    elif stage == "doc_name":
        if "," not in message:
            return {"text": "❗ Please enter as Last Name, First Name."}
        session_dict[user]["doc_name"] = [name.strip() for name in message.split(",")]
        session_dict[user]["stage"] = "emergency_email"
        save_sessions(session_dict)
        return {"text": questions["emergency_email"]}

    elif stage == "emergency_email":
        if "@" not in message or "." not in message:
            return {"text": "❗ Please enter a valid email."}
        session_dict[user]["emergency_email"] = message
        session_dict[user]["stage"] = "daily"
        save_sessions(session_dict)

        buttons = [
            {"type": "button", "text": "Daily wellness check 📝", "msg": "Begin my daily wellness check for today", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Daily wellness check"},
            {"type": "button", "text": "Weekly update  🗞️", "msg": "Generate my weekly update", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Weekly update"},
            {"type": "button", "text": "General question 💬", "msg": "I have a general question", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "General question"}
        ]
        return {
            "text": f"Onboarding complete! 🎉\n\nExplore some of DocBot's features below.",
            "attachments": [{"collapsed": False,"color": "#e3e3e3", "actions": buttons}]
        }
    


### --- GENERAL QUESTION FUNCTION --- ###
def llm_general(message, user, session_dict):
    """Handles any general user questions
    """
    print("IN LLM GENERAL")

     # 1. Pull out the session values you need
    sid = session_dict[user]["session_id"]
    condition = session_dict[user]["condition"]

    # 2) fill in your template
    system1 = general_system_template.format(
        condition=condition
    )

    # 3) call your LLM with that system prompt
    response = generate(
        model="4o-mini",
        system=system1,
        query=message,
        temperature=0.7,
        lastk=session_dict[user]["history"],
        session_id=sid,
        rag_usage=False
    )
    response_text = response.get("response", "⚠️ Sorry, I couldn't process that. Could you rephrase?").strip() if isinstance(response, dict) else response.strip()
    
    response_obj = {
        "text": response_text,
        "attachments": [{"collapsed": False,
        "color": "#e3e3e3",
        "actions": [{"type": "button", "text": "Main menu 🏠", "msg": "Return to main menu", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "choose_yes"}]}]
    }

    save_sessions(session_dict)
    return response_obj


### --- DAILY INTERACTION FUNCTION --- ###
def llm_daily(message, user, session_dict):
    """Handles routine wellness check: 
        - Collects daily symptoms through personalized questions
        - Formulates advice and passes to QA agent before returning
        - Activates expert in the loop if it determines symptoms are abnormal.
    """
    print("IN LLM DAILY")

    sid = session_dict[user]["session_id"]
    first_name = user.split('.')[0].capitalize()
    condition = session_dict[user]["condition"]
    doc_name = session_dict[user]["doc_name"][0]
    emergency_email = session_dict[user]["emergency_email"]
    meds = session_dict[user]["medications"]

    # build a nice meds string
    if len(meds) == 1:
        formatted_meds = meds[0]
    elif len(meds) == 2:
        formatted_meds = f"{meds[0]} and {meds[1]}"
    else:
        formatted_meds = ", ".join(meds[:-1]) + f", and {meds[-1]}"

    # inject into your daily‐system template
    system2 = daily_system_template.format(
        first_name=first_name,
        condition=condition,
        formatted_meds=formatted_meds,
        doc_name=doc_name,
        emergency_email=emergency_email
    )

    # now call the LLM
    response = generate(
        model="4o-mini",
        system=system2,
        query=message,
        temperature=0.7,
        lastk=session_dict[user]["history"],
        session_id=sid,
        rag_usage=True,
        rag_threshold='0.5',
        rag_k=5
    )

    response_text = response.get("response", "⚠️ Sorry, I couldn't process that. Could you rephrase?").strip() if isinstance(response, dict) else response.strip()

    response_obj = {
        "text": response_text,
    }

    advice = ""
    next_question = ""

    # Extract advice (everything from 'DocBot's Advice:' to just before the next 'Question')
    advice_match = re.search(r"DocBot's Advice:(.*?)(?=Question \d|$)", response_text, re.DOTALL | re.IGNORECASE)
    # Extract the next question (starting with 'Question ')
    question_match = re.search(r"(Question \d.*)", response_text, re.DOTALL)
    advice = advice_match.group(1).strip() if advice_match else "Unable to extract advice."
    if advice != "Unable to extract advice.":
        next_question = question_match.group(1).strip() if question_match else ""
    

    # When to send to QA agent?
    if "docbot's advice" in response_text.lower():
        qa_response = qa_agent(message, advice, user, session_dict)

        if "approved" in qa_response.lower():
            print("in approved")
            pass    
        elif "needs revision" in qa_response.lower():
            match = re.search(r"Suggested revision:\s*(.*)", qa_response)
            if match:
                advice = match.group(1).strip()
                if "next_question" != "END":
                    response_obj["text"] = "DocBot's Advice: " + advice + "\n\n" + next_question
                else:
                    response_obj["text"] = advice
            else:
                print("No revised message found.")

        elif "rejected" in qa_response.lower():
            response_obj["text"] = f"I'm not sure how to evaluate those symptoms. 🙁 Would you like to contact Dr. {session_dict[user]['doc_name'][0]} at {session_dict[user]['emergency_email']}?"


    if "would you like to contact dr." in response_text.lower():
        buttons = [
            {"type": "button", "text": "Yes ✅", "msg": "Yes_email", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "choose_yes"},
            {"type": "button", "text": "No ❌", "msg": "No_email", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "choose_no"}
        ]
        response_obj = {
            "text": response_text + "\n" + "📧 Do you want to contact your doctor?",
            "attachments": [{"collapsed": False, "color": "#e3e3e3", "actions": buttons}]
        }
    

    if "Subject of email:" in response_text:
        match = re.search(r"Subject of email[:*\s]*(\S.*)", response_text)  # Capture actual text after "*Subject of email:*"
        if match:
            subject = match.group(1).strip()  # Remove extra spaces
            session_dict[user]['email_subject'] = subject
            save_sessions(session_dict)
    if "Content of email:" in response_text:
        match = re.search(r"Content of email:(.*?)(?=Please confirm\d|$)", response_text, re.DOTALL | re.IGNORECASE)
        if match:
            content = match.group(1).strip()  # Remove extra spaces
            session_dict[user]['email_content'] = content
            save_sessions(session_dict)
    
    if "daily doses" in response_text:
        buttons = [
            {"type": "button", "text": "Yes ✅", "msg": "Yes, I have taken my daily medication", "msg_in_chat_window": True, "msg_processing_type_": "sendMessage", "button_id": "Yes"},
            {"type": "button", "text": "No ❌", "msg": "No, I have not taken my daily medication yet", "msg_in_chat_window": True, "msg_processing_type_": "sendMessage", "button_id": "No i have not"}
        ]
    
        response_obj = {
            "text": response_text,
            "attachments": [{"collapsed": False, "color": "#e3e3e3", "actions": buttons}]
        }
    
    if message == "Yes_email":
        buttons = [
            {"type": "button", "text": "1️⃣ Generate a summary of my symptoms", "msg": "Draft a detailed formal email with a summary of my symptoms of today", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Generate a summary of my symptoms"},
            {"type": "button", "text": "2️⃣ Ask my doctor for specific medical advice", "msg": "Draft a detailed formal email to ask my doctor for specific medical advice about my symptoms", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Ask my doctor for specific medical advice"},
            {"type": "button", "text": "3️⃣ Schedule an appointment", "msg": "Draft a detailed formal email to schedule an appointment with my doctor. In the email, ask them for availability and provide them with my current symptoms", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Schedule an appointment"}
        ]
    
        response_obj = {
            "text": "Great! Let me know what you want to discuss in your email.\n\nHere are some content ideas you might consider:\n1️⃣ Generate a summary of my symptoms\n2️⃣ Ask my doctor for specific medical advice\n3️⃣ Express interest in scheduling a consultation/appointment",
            "attachments": [{"collapsed": False,"color": "#e3e3e3","actions": buttons}]
        }
    

    if "Please confirm " in response_text:
        buttons = [
            {"type": "button", "text": "Send it! ✅", "msg": "Yes_confirm", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "choose_yes"},
            {"type": "button", "text": "Edit subject ✍️", "msg": "I want to edit the subject of the email", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "choose_subject"},
            {"type": "button", "text": "Edit content ✍️", "msg": "I want to edit the content of the email", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "choose_content"}
        ]
    
        response_obj = {
            "text": response_text,
            "attachments": [{"collapsed": False,"color": "#e3e3e3","actions": buttons}]
        }
    
    if "Yes_confirm" in message:
        subject, content = session_dict[user]['email_subject'], session_dict[user]['email_content']
        send_email(session_dict[user]["emergency_email"], subject, content)
        session_dict[user]['email_subject'] = session_dict[user]['email_content'] = ""
        save_sessions(session_dict)

        return { 
            "text": f"📧 Email successfully sent to Dr. {session_dict[user]['doc_name'][0]} at {session_dict[user]['emergency_email']}!\n\nIf there's anything else you need, don't hesitate to ask! 😊",
            "attachments": [{"collapsed": False, "color": "#e3e3e3", "actions": [{"type": "button", "text": "Main menu 🏠", "msg": "Return to main menu", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "choose_yes"}]}]
        }


    # Append Main menu button to every message
    if "attachments" not in response_obj:
        response_obj["attachments"] = []

    # Ensure the Main menu button is added after other buttons
    response_obj["attachments"].append({
        "collapsed": False,
        "color": "#e3e3e3",
        "actions": [{"type": "button", "text": "Main menu 🏠", "msg": "Return to main menu", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "choose_yes"}]
    })

    save_sessions(session_dict)
    return response_obj



def qa_agent(message, agent_response, user, session_dict):
    sid = session_dict[user]["session_id"]

    response = generate(
        model="4o-mini",
        system=f"""
        ### Role:
            You are a highly knowledgeable and cautious **quality assurance assistant** reviewing medical advice 
            generated by another AI agent. Your primary responsibility is to **evaluate the safety, appropriateness, 
            and tone** of the medical responses provided to users with chronic conditions (e.g., diabetes, Crohn's disease).
            ---
            ### Objectives:
            1. **Ensure Medical Safety**
            - Verify that the advice does not suggest a diagnosis or prescribe treatment.
            - Confirm that the response encourages users to consult a healthcare provider for serious or unclear symptoms.
            - Flag any misinformation, unsupported claims, or dangerous suggestions based on your knowledge of the condition.
            - The response should always cite where the medical advice is from. For example, “According to ADA 2024 guidelines…”.
            - Don't ask further follow up questions in your response.
            
            2. **Check Relevance and Completeness**
            - Ensure the advice is relevant to the user's condition and symptoms.
            - Identify if any critical symptoms or next steps were missed.
            - Confirm the message includes compassionate guidance for users in distress.
            - Ensure the chatbot's advice is inclusive across demographics (e.g., different age groups, pregnancy, comorbidities).

            3. **Evaluate Tone and Language**
            - Ensure the tone is empathetic, respectful, and calming.
            - Confirm the language is clear, concise, and free of technical jargon unless properly explained.
            ---
            ### Guidelines:
            - If the advice is **safe, appropriate, and helpful**, respond with:
            `"Approved. The message is safe, relevant, and well-phrased."`

            - If the advice has **minor issues**, suggest a revision:
            `"Needs revision: [brief explanation]. Suggested revision: [revised message]"`

            - If the advice contains **major issues** and cannot be revised, flag it:
            `"Rejected: [brief explanation of the safety concern or misinformation]"`
            ---
            ### Input:
            You will be given:
            - The **user's condition** and reported **symptoms**.
            - The **original advice** generated by the primary AI agent.

            ---

            ### Example:
            **User Condition:** Diabetes  
            **User Message:** “I feel dizzy and confused.”  
            **Primary Agent Response:**  
            _"You might be experiencing low blood sugar. Try drinking juice or eating something sweet. If it doesn't help, take insulin or call your doctor."_

            **Your Evaluation:**  
            Rejected: Suggesting insulin without knowing blood sugar levels could be dangerous. Low blood sugar should not be treated with insulin.  

            Suggested revision:  
            "Dizziness and confusion can be signs of low blood sugar. If you suspect this, try eating something with sugar, like juice or candy. If you don't feel better soon, seek medical help immediately."
        """,

        query=f"User Condition: {session_dict[user]['condition']}. User Message: {message}. Primary Agent Response: {agent_response}",
        temperature=0.3,
        lastk=session_dict[user]["history"],
        session_id=sid,
        rag_usage=True,
        rag_threshold='0.7',
        rag_k=10
    )

    response_text = response.get("response", "⚠️ Sorry, I couldn't process that. Could you rephrase?").strip() if isinstance(response, dict) else response.strip()
    print(f"qa agent said: {response_text}")

    return response_text
    
    
    
# def run_scheduler():
#     """
#     Runs an infinite loop to check and execute scheduled jobs.
#     It should be run in a separate thread to avoid blocking the Flask server.
#     Executes scheduled tasks every minute.
#     """
#     while True:
#         schedule.run_pending()
#         time.sleep(60)  # Check every minute


# def scheduled_task():
#     """
#     This function is scheduled to run every day at 9 AM.
#     It loads user sessions, calls `llm_daily()` for each user, and saves the results.
#     Modifies `session_dict` and saves session data.
#     """
#     session_dict = load_sessions()  # Load session data
#     for user in session_dict.keys():
#         llm_daily("Daily scheduled message", user, session_dict)  # Call LLM function
#     save_sessions(session_dict)  # Save updated sessions


# schedule.every().day.at("09:00").do(scheduled_task)

# # Start the scheduler in a separate background thread
# scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
# scheduler_thread.start()




### --- FLASK ROUTE TO HANDLE USER REQUESTS --- ###
# """Handles user messages and manages session storage."""
@app.route('/query', methods=['POST'])
def main():
    data = request.get_json()
    message = data.get("text", "").strip()
    user = data.get("user_name", "Unknown")

    # Load sessions at the beginning of each request
    session_dict = load_sessions()
    
    print("Current session dict:", session_dict)
    print("Current user:", user)
    print("Message: ", message)

    # Initialize user session if it doesn't exist
    if user not in session_dict or "restart" in message.lower():
        print("new user", user)
        session_dict[user] = {
            "session_id": f"{user}-session",
            "stage": "start",
            "condition": "",
            "age": 0,
            "weight": 0,
            "medications": [],
            "emergency_email": "",
            "news_pref": "",
            "history": 1
        }
        save_sessions(session_dict)  # Save immediately after creating new session
        rag_upload(session_dict[user]["condition"], user, session_dict)
        print("🔄 Restarted onboarding.")


    response = ""
    onboarding = ["start", "age", "weight", "condition", "medications", "emergency_email", "doc_name"]
    if session_dict[user]["stage"] in onboarding:
        response = first_interaction(message, user, session_dict)
        session_dict[user]["history"] = 1
        save_sessions(session_dict)

    elif message == "Return to main menu":
        session_dict[user]["stage"] = ""
        save_sessions(session_dict)
        buttons = [
            {"type": "button", "text": "Daily wellness 📝", "msg": "Begin my daily wellness check for today", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Daily wellness check"},
            {"type": "button", "text": "Weekly update 🗞️", "msg": "Generate my weekly update", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Weekly update"},
            {"type": "button", "text": "General question 💬", "msg": "I have a general question", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "General question"}
        ]
    
        return {
            "text": f"🏠 DocBot main menu:",
            "attachments": [{"collapsed": False,"color": "#e3e3e3", "actions": buttons}]
        }

    elif message == "Generate my weekly update":
        session_dict[user]["stage"] = "weekly"
        save_sessions(session_dict)
        response = weekly_update_internal(message, user, session_dict)

    elif message == "Begin my daily wellness check for today":
        # schedule.every().day.at("09:00").do(llm_daily)
        session_dict[user]["stage"] = "daily"
        save_sessions(session_dict)
        response = llm_daily(message, user, session_dict)

    elif message == "I have a general question":
        session_dict[user]["stage"] = "general"
        save_sessions(session_dict)
        response = llm_general(message, user, session_dict)

    elif session_dict[user]["stage"] == "daily":
        response = llm_daily(message, user, session_dict)

    elif session_dict[user]["stage"] == "general":
        response = llm_general(message, user, session_dict)

    elif session_dict[user]["stage"] == "weekly":
        response = weekly_update_internal(message, user, session_dict)

    else:
        buttons = [
            {"type": "button", "text": "Daily wellness check 📝", "msg": "Begin my daily wellness check for today", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Daily wellness check"},
            {"type": "button", "text": "Weekly update 🗞️", "msg": "Generate my weekly update", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "Weekly update"},
            {"type": "button", "text": "General question 💬", "msg": "I have a general question", "msg_in_chat_window": True, "msg_processing_type": "sendMessage", "button_id": "General question"}
        ]
    
        first_name = user.split('.')[0].capitalize()
        response = {
            "text": f"Hi {first_name}! Welcome back to DocBot. How can I help you today?",
            "attachments": [{"collapsed": False,"color": "#e3e3e3", "actions": buttons}]
        }
    

    # Save session data at the end of the request
    session_dict[user]["history"] += 1
    save_sessions(session_dict)

    return jsonify(response)


### --- RUN THE FLASK APP --- ###
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
