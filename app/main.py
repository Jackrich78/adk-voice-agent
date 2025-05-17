import asyncio
import base64
import json
import os
from pathlib import Path
from typing import AsyncIterable

from dotenv import load_dotenv
from fastapi import FastAPI, Query, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.adk.events.event import Event
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types # Keep for types.SpeechConfig if used
from jarvis.agent import root_agent # This will be your Jarvis agent
from starlette.websockets import WebSocketDisconnect, WebSocketState # For logging and checks

# Load Gemini API Key
load_dotenv()

APP_NAME = "ADK Streaming example" # As per original
session_service = InMemorySessionService()


# This start_agent_session function is now based on the author's original structure,
# but adapted for "Audio In -> Text Out" and includes our debug logging.
def start_agent_session(session_id, is_audio_input_from_client=False): # Renamed param for clarity
    """Starts an agent session"""

    print(f"[SESSION {session_id}]: Creating ADK Session. Input type from client: {'AUDIO' if is_audio_input_from_client else 'TEXT'}")

    session = session_service.create_session(
        app_name=APP_NAME,
        user_id=session_id,
        session_id=session_id,
    )

    runner = Runner(
        app_name=APP_NAME,
        agent=root_agent, # This should be your Jarvis agent
        session_service=session_service,
    )

    # --- Configuration for "Audio In -> Text Out" or "Text In -> Text Out" ---
    # Agent's response modality is ALWAYS TEXT in this simplified setup.
    agent_output_modality = "TEXT"
    config_parts = {"response_modalities": [agent_output_modality]}

    if is_audio_input_from_client:
        print(f"[SESSION {session_id}]: Configuring RunConfig for AUDIO input (user) -> TEXT output (agent: {root_agent.name}).")
        # Request transcript of what the USER said (comes back as text events via live_events)
        config_parts["output_audio_transcription"] = {} 
        # No speech_config needed here, as the agent's output modality is always TEXT.
        # If you wanted the agent to respond with AUDIO, you would add speech_config here.
    else: # Text input mode
        print(f"[SESSION {session_id}]: Configuring RunConfig for TEXT input (user) -> TEXT output (agent: {root_agent.name}).")
    
    print(f"[SESSION {session_id}]: Final config parts for RunConfig: {config_parts}")
    run_config = RunConfig(**config_parts)
    
    live_request_queue = LiveRequestQueue()
    
    print(f"[SESSION {session_id}]: Attempting runner.run_live for agent '{root_agent.name}' (client_input_audio={is_audio_input_from_client}, agent_output_modality={agent_output_modality})")
    try:
        live_events = runner.run_live(
            session=session,
            live_request_queue=live_request_queue,
            run_config=run_config,
        )
        print(f"[SESSION {session_id}]: runner.run_live call completed successfully for agent '{root_agent.name}'.")
        return live_events, live_request_queue 
    except Exception as e:
        print(f"[SESSION {session_id}]: ERROR during runner.run_live for agent '{root_agent.name}': {e}")
        import traceback
        traceback.print_exc()
        raise # Propagate error to stop websocket_endpoint if run_live fails


# This agent_to_client_messaging function includes our detailed logging.
async def agent_to_client_messaging(
    websocket: WebSocket, live_events: AsyncIterable[Event | None]
):
    """Agent to client communication"""
    session_id_for_log = websocket.path_params['session_id']
    print(f"[AGENT->CLIENT {session_id_for_log}]: Starting task. Iterating over live_events...")
    events_received_count = 0
    try:
        async for event in live_events: 
            events_received_count += 1
            print(f"[AGENT->CLIENT {session_id_for_log}]: Received event #{events_received_count} from live_events: {event}")

            if event is None:
                print(f"[AGENT->CLIENT {session_id_for_log}]: Event is None, skipping.")
                continue

            if websocket.client_state == WebSocketState.DISCONNECTED:
                print(f"[AGENT->CLIENT {session_id_for_log}]: WebSocket is disconnected, cannot send. Breaking loop.")
                break

            if event.turn_complete or event.interrupted:
                message = {
                    "turn_complete": event.turn_complete,
                    "interrupted": event.interrupted,
                }
                print(f"[AGENT->CLIENT {session_id_for_log}]: Sending turn_complete/interrupted: {message}")
                await websocket.send_text(json.dumps(message))
                continue # Important: after turn_complete, no more parts for this turn

            part = event.content and event.content.parts and event.content.parts[0]
            if not part:
                # print(f"[AGENT->CLIENT {session_id_for_log}]: Event has no part, skipping.") # Can be noisy
                continue
            if not isinstance(part, types.Part):
                print(f"[AGENT->CLIENT {session_id_for_log}]: Event part is not a valid types.Part, skipping: {type(part)}")
                continue

            # Handle TEXT part (either transcript from user audio, or agent's text response)
            if part.text: # Check if there's text, event.partial handles streaming
                is_partial_event = hasattr(event, 'partial') and event.partial # Check if ADK event is partial
                message = {
                    "mime_type": "text/plain",
                    "data": part.text,
                    "role": "model",
                    "partial": is_partial_event # EXPLICITLY SEND PARTIAL STATUS
                }
                if event.partial:
                    print(f"[AGENT->CLIENT {session_id_for_log}]: Sending partial text: {part.text[:70]}...")
                else: # Final text part for this event
                    print(f"[AGENT->CLIENT {session_id_for_log}]: Sending final text part: {part.text[:70]}...")
                await websocket.send_text(json.dumps(message))
            
            # Handle AUDIO part (agent's audio response - NOT USED in current "Text Out" config, but good to keep)
            is_agent_audio_output = (
                part.inline_data
                and part.inline_data.mime_type
                and part.inline_data.mime_type.startswith("audio/pcm")
            )
            if is_agent_audio_output:
                audio_data = part.inline_data.data
                if audio_data:
                    message = {
                        "mime_type": "audio/pcm",
                        "data": base64.b64encode(audio_data).decode("ascii"),
                        "role": "model",
                    }
                    print(f"[AGENT->CLIENT {session_id_for_log}]: Sending agent audio data: {len(audio_data)} bytes.")
                    await websocket.send_text(json.dumps(message))

        print(f"[AGENT->CLIENT {session_id_for_log}]: Finished iterating over live_events. Total events received: {events_received_count}")

    except WebSocketDisconnect:
        print(f"[AGENT->CLIENT {session_id_for_log}]: WebSocket disconnected during processing.")
    except Exception as e:
        print(f"[AGENT->CLIENT {session_id_for_log}]: Error in event processing: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print(f"[AGENT->CLIENT {session_id_for_log}]: Exiting task. Total events processed: {events_received_count}")


# This client_to_agent_messaging function includes our detailed logging and ACK logic.
async def client_to_agent_messaging(
    websocket: WebSocket, live_request_queue: LiveRequestQueue
):
    """Client to agent communication"""
    session_id_for_log = websocket.path_params['session_id']
    print(f"[CLIENT->AGENT {session_id_for_log}]: Starting task.")
    first_audio_chunk_received = False
    try:
        while True:
            message_json = await websocket.receive_text()
            message = json.loads(message_json)
            mime_type = message["mime_type"]
            data = message["data"]
            role = message.get("role", "user") 

            if mime_type == "text/plain":
                content = types.Content(role=role, parts=[types.Part.from_text(text=data)]) # Original way to create Part for text
                live_request_queue.send_content(content=content)
                print(f"[CLIENT->AGENT {session_id_for_log}]: Sent text to ADK queue: {data}")
            elif mime_type == "audio/pcm":
                if not first_audio_chunk_received:
                    first_audio_chunk_received = True
                    ack_message = {"mime_type": "application/json", "type": "status", "data": "audio_received_processing", "role": "system"}
                    if websocket.client_state == WebSocketState.CONNECTED:
                        print(f"[SERVER ACK {session_id_for_log}]: Sending 'audio_received_processing' to client.")
                        await websocket.send_text(json.dumps(ack_message))
                    else:
                        print(f"[SERVER ACK {session_id_for_log}]: WebSocket disconnected, cannot send 'audio_received_processing' ack.")
                
                decoded_data = base64.b64decode(data)
                live_request_queue.send_realtime(types.Blob(data=decoded_data, mime_type=mime_type))
                # print(f"[CLIENT->AGENT {session_id_for_log}]: Sent audio chunk to ADK queue: {len(decoded_data)} bytes") # Can be noisy
            
            elif mime_type == "application/json" and message.get("type") == "control": # From our app.js modification
                if data == "audio_input_complete":
                    print(f"[CLIENT->AGENT {session_id_for_log}]: Received 'audio_input_complete' signal from client.")
                    # This message signals the client has stopped sending audio.
                    # The ADK should detect end-of-speech from the audio stream itself.
                    # We can optionally send a status back to client here if needed.
                    status_msg = {"mime_type": "application/json", "type": "status", "data": "server_processing_full_audio", "role": "system"}
                    if websocket.client_state == WebSocketState.CONNECTED:
                         await websocket.send_text(json.dumps(status_msg))
                else:
                    print(f"[CLIENT->AGENT {session_id_for_log}]: Unknown control message: {data}")
            else:
                print(f"[CLIENT->AGENT {session_id_for_log}]: Mime type not supported: {mime_type}")
                # Optionally raise ValueError, or just log and continue
    except WebSocketDisconnect:
        print(f"[CLIENT->AGENT {session_id_for_log}]: WebSocket disconnected.")
    except Exception as e:
        print(f"[CLIENT->AGENT {session_id_for_log}]: Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print(f"[CLIENT->AGENT {session_id_for_log}]: Exiting task.")


# FastAPI app setup (identical to original)
app = FastAPI()
STATIC_DIR = Path("static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

# websocket_endpoint (identical to original, but uses our modified start_agent_session)
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    is_audio: str = Query(...), # This comes from app.js when it connects
):
    await websocket.accept()
    print(f"Client #{session_id} connected, client_requested_audio_input_mode: {is_audio}")

    # Call our modified start_agent_session
    # It now internally decides agent_output_modality is always TEXT
    # and only adds output_audio_transcription if is_audio == "true"
    session_live_events, session_live_request_queue = start_agent_session(
        session_id, is_audio_input_from_client=(is_audio == "true")
    )

    if session_live_events is None or session_live_request_queue is None:
        print(f"Client #{session_id}: Failed to start agent session. Closing WebSocket.")
        await websocket.close(code=1011) # Internal server error
        return

    agent_to_client_task = asyncio.create_task(
        agent_to_client_messaging(websocket, session_live_events)
    )
    client_to_agent_task = asyncio.create_task(
        client_to_agent_messaging(websocket, session_live_request_queue)
    )
    
    try:
        await asyncio.gather(agent_to_client_task, client_to_agent_task)
    except Exception as e:
        print(f"Client #{session_id}: Error in gathered tasks: {e}")
        # Ensure tasks are cancelled if gather fails
        agent_to_client_task.cancel()
        client_to_agent_task.cancel()
        try:
            await agent_to_client_task
        except asyncio.CancelledError:
            pass
        try:
            await client_to_agent_task
        except asyncio.CancelledError:
            pass
    finally:
        print(f"Client #{session_id} disconnected and tasks cleaned up.")

