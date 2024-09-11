import os
import requests
import json
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import BEARER_TOKEN, BASIC_AUTH, FOLDER_ID, PROJECT_ID, API_URL
from instructions import INSTRUCTION_MAP

# Setup logging to be thread-safe

def setup_logging():
    # Log to the console (stdout) instead of a file
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(threadName)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()]
    )


def start_test(test_name, instructions):
    payload = {"instructions": instructions}
    headers = {
        'accept': 'application/json',
        'authorization': f'Bearer {BEARER_TOKEN}',
        'content-type': 'application/json',
    }
    response = requests.post(API_URL, headers=headers, json=payload)

    if response.status_code == 200:
        test_data = response.json()
        test_id = test_data.get('test_id')
        fqdn = test_data.get('fqdn')
        logging.info(f"Test '{test_name}' started successfully with Test ID: {test_id}")
        return test_id, fqdn
    else:
        logging.error(f"Failed to start test '{test_name}'. Status code: {response.status_code}")
        return None, None

def fetch_final_state(test_id, fqdn):
    url = f'https://{fqdn}/web-agent/sse?testId={test_id}&sessionType=web'
    headers = {'Accept': 'text/event-stream', 'Authorization': f'Basic {BASIC_AUTH}', 'Connection': 'keep-alive'}

    response = requests.get(url, headers=headers, stream=True)
    if response.status_code != 200:
        logging.error(f"Failed to connect to SSE for Test ID: {test_id}. Status code: {response.status_code}")
        return None

    for line in response.iter_lines():
        if line:
            decoded_line = line.decode('utf-8').strip()
            if decoded_line.startswith("data: "):
                json_data = decoded_line[6:]
                event_data = json.loads(json_data)
                system_state = event_data.get('system_state', {}).get('id')
                if system_state == 'idle':
                    logging.info(f"Test ID {test_id}: System state is 'idle'. Test completed successfully.")
                    return 'idle'
                elif system_state == 'error':
                    error_data = event_data.get('sync_data', [])
                    for instruction_data in error_data:
                        if instruction_data.get('status') == 'error':
                            failed_instruction = instruction_data.get('instruction', 'Unknown instruction')
                            error_reason = "Unknown error"
                            debugging_logs = instruction_data.get('debugging_logs', [])
                            for log in debugging_logs:
                                if 'error' in log:
                                    error_reason = log['error']
                                    break
                            logging.error(f"Test ID {test_id} failed at instruction: '{failed_instruction}' with error: {error_reason}")
                            return 'error', failed_instruction, error_reason
    return None

def stop_test(test_id):
    url = f'https://auteur-pre-prod-test-manager.lambdatestinternal.com/api/atm/v1/test/{test_id}'
    headers = {'accept': 'application/json', 'authorization': f'Basic {BASIC_AUTH}'}

    response = requests.delete(url, headers=headers)
    if response.status_code == 200:
        logging.info(f"Test ID {test_id} stopped successfully.")
    else:
        logging.error(f"Failed to stop Test ID {test_id}. Status code: {response.status_code}")

def save_test(test_id, test_name, instructions):
    url = f'https://auteur-pre-prod-test-manager.lambdatestinternal.com/api/atm/v1/test/{test_id}'
    total_steps = len(instructions)
    payload = {
        "project_id": PROJECT_ID,
        "folder_id": FOLDER_ID,
        "test_name": test_name,
        "description": "hola amigo",
        "total_steps": total_steps
    }
    headers = {
        'Authorization': f'Bearer {BEARER_TOKEN}',
        'Content-Type': 'application/json'
    }

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        logging.info(f"Test ID {test_id} saved successfully.")
    else:
        logging.error(f"Failed to save Test ID {test_id}. Status code: {response.status_code}")

def run_test(test_name, instructions):
    logging.info(f"Running test '{test_name}'...")
    test_id, fqdn = start_test(test_name, instructions)
    if not test_id or not fqdn:
        return

    state = fetch_final_state(test_id, fqdn)
    if state == 'idle':
        logging.info(f"Test '{test_name}' completed successfully.")
        stop_test(test_id)
        save_test(test_id, test_name, instructions)
    elif state and state[0] == 'error':
        failed_instruction, error_message = state[1], state[2]
        logging.error(f"Test '{test_name}' failed at instruction '{failed_instruction}' with error: {error_message}.")
        stop_test(test_id)

def main():
    setup_logging()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run_test, test_name, instructions) for test_name, instructions in INSTRUCTION_MAP.items()]
        for future in as_completed(futures):
            future.result()

if __name__ == "__main__":
    main()
