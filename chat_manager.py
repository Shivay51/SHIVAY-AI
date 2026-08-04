import json
import os

FILE_NAME = "chat_ids.json"


def load_chats():

    if not os.path.exists(FILE_NAME):

        with open(FILE_NAME, "w") as f:
            json.dump({"groups": []}, f, indent=4)

    with open(FILE_NAME, "r") as f:

        return json.load(f)


def save_chats(data):

    with open(FILE_NAME, "w") as f:

        json.dump(data, f, indent=4)


def get_all_chats():

    return load_chats()["groups"]


def add_chat(chat_id):

    data = load_chats()

    if chat_id not in data["groups"]:

        data["groups"].append(chat_id)

        save_chats(data)

        return True

    return False


def remove_chat(chat_id):

    data = load_chats()

    if chat_id in data["groups"]:

        data["groups"].remove(chat_id)

        save_chats(data)

        return True

    return False