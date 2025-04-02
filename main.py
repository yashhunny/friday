import json
import traceback
import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from twilio.twiml.voice_response import VoiceResponse, Connect
from elevenlabs import ElevenLabs
from twilio_audio_interface import TwilioAudioInterface
from elevenlabs.conversational_ai.conversation import Conversation
import time
import hmac
from hashlib import sha256
import requests

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI()

# Initialize ElevenLabs client
ELEVEN_LABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_LABS_AGENT_ID = os.getenv("AGENT_ID")
INTERCOM_API_KEY = os.getenv("INTERCOM_API_KEY")
eleven_labs_client = ElevenLabs(api_key=ELEVEN_LABS_API_KEY)
secret = "secret"
admin_id = 123
smrtlite_id = 567

intercom_url = "https://api.intercom.io/conversations"


@app.get("/")
async def root():
    return {"message": "Twilio-ElevenLabs Integration Server"}


@app.post("/twilio/inbound_call")
async def handle_incoming_call(request: Request):
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "Unknown")
    from_number = form_data.get("From", "Unknown")
    print(f"Incoming call: CallSid={call_sid}, From={from_number}")

    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{request.url.hostname}/media-stream")
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")


@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket connection opened")

    audio_interface = TwilioAudioInterface(websocket)
    eleven_labs_client = ElevenLabs(api_key=ELEVEN_LABS_API_KEY)

    try:
        conversation = Conversation(
            client=eleven_labs_client,
            agent_id=ELEVEN_LABS_AGENT_ID,
            requires_auth=True,  # Security > Enable authentication
            audio_interface=audio_interface,
            callback_agent_response=lambda text: print(f"Agent: {text}"),
            callback_user_transcript=lambda text: print(f"User: {text}"),
        )

        conversation.start_session()
        print("Conversation started")

        async for message in websocket.iter_text():
            if not message:
                continue
            await audio_interface.handle_twilio_message(json.loads(message))

    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception:
        print("Error occurred in WebSocket handler:")
        traceback.print_exc()
    finally:
        try:
            conversation.end_session()
            conversation.wait_for_session_end()
            print("Conversation ended")
        except Exception:
            print("Error ending conversation session:")
            traceback.print_exc()


@app.post("/post-webhook")
async def receive_message(request: Request):
    payload = await request.body()
    print(f"Post-Data: {payload}")
    headers = request.headers.get("elevenlabs-signature")
    if headers is None:
        return
    timestamp = headers.split(",")[0][2:]
    hmac_signature = headers.split(",")[1]
    # Validate timestamp
    tolerance = int(time.time()) - 30 * 60
    if int(timestamp) < tolerance:
        return
    # Validate signature
    full_payload_to_sign = f"{timestamp}.{payload.decode('utf-8')}"
    mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=full_payload_to_sign.encode("utf-8"),
        digestmod=sha256,
    )
    digest = "v0=" + mac.hexdigest()
    if hmac_signature != digest:
        return
    # Continue processing
    print("Signature verification succeeded")

    phone_number = payload["data"]["metadata"]["phone_call"]["external_number"]
    transcript = payload["data"]["analysis"]["transcript_summary"]
    should_support = payload["data"]["analysis"]["evalutation_criteria_results"]["should_support"]
    
    contact_id = create_intercom_contact(phone_number)
    conversation_id = create_intercom_conversation(transcript, contact_id)
    
    assign_conversation(conversation_id, admin_id, smrtlite_id)
    
    if(not should_support):
        close_conversation(conversation_id, admin_id)

    return {"status": "received"}


def create_intercom_contact(phone_number):
    import requests

    url = "https://api.intercom.io/contacts"

    payload = {"external_id": phone_number, "phone": phone_number}

    headers = {
        "Content-Type": "application/json",
        "Intercom-Version": "2.13",
        "Authorization": f"Bearer {INTERCOM_API_KEY}",
    }

    response = requests.post(url, json=payload, headers=headers)

    data = response.json()
    print(data)
    return data["id"]


def create_intercom_conversation(transcript, user_id):
    url = "https://api.intercom.io/conversations"
    payload = {"from": {"type": "user", "id": user_id}, "body": transcript}

    headers = {
        "Content-Type": "application/json",
        "Intercom-Version": "2.13",
        "Authorization": f"Bearer {INTERCOM_API_KEY}",
    }

    response = requests.post(url, json=payload, headers=headers)

    data = response.json()
    print(data)
    return data["id"]
    
def close_conversation(conversation_id, admin_id):
    url = "https://api.intercom.io/conversations/" + conversation_id + "/parts"

    payload = {
    "message_type": "close",
    "type": "admin",
    "admin_id": admin_id
    }

    headers = {
    "Content-Type": "application/json",
    "Intercom-Version": "2.13",
    "Authorization": f"Bearer {INTERCOM_API_KEY}"
    }

    response = requests.post(url, json=payload, headers=headers)

    data = response.json()
    print(data)
    
def assign_conversation(conversation_id, admin_id, assignee_id):
    url = "https://api.intercom.io/conversations/" + conversation_id + "/parts"

    payload = {
    "message_type": "assignment",
    "type": "admin",
    "admin_id": admin_id,
    "assignee_id": assignee_id
    }

    headers = {
    "Content-Type": "application/json",
    "Intercom-Version": "2.13",
    "Authorization": f"Bearer {INTERCOM_API_KEY}"
    }

    response = requests.post(url, json=payload, headers=headers)

    data = response.json()
    print(data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
