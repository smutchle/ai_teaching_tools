import os
from openai import OpenAI
import re


class OpenAIChatBot:
    def __init__(self, api_key=None, model="gpt-5", temperature=0.0, use_chat_history=False):
        self.messages = []
        self.model = model
        self.temperature = temperature
        self.use_chat_history = use_chat_history

        # Initialize the OpenAI client
        self.client = OpenAI(api_key=api_key)

    def clean_string(self, text):
        if not isinstance(text, str):
            return text

        # Define ranges of valid characters more strictly
        valid_controls = [
            (0x09, 0x09),  # Horizontal tab
            (0x0A, 0x0A),  # Line feed
            (0x0D, 0x0D),  # Carriage return
            (0x20, 0x7E),  # Printable ASCII
            (0x0080, 0xD7FF),  # Basic Multilingual Plane (excluding surrogates)
            (0xE000, 0xFFFD),  # BMP Private Use Area and beyond (excluding special)
        ]

        # Convert to bytes first to catch invalid UTF-8
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            return ""

        # Filter characters and build result
        result = []
        for char in text:
            code = ord(char)
            is_valid = any(start <= code <= end for start, end in valid_controls)
            if is_valid:
                result.append(char)

        return "".join(result)

    def extract_markdown_content(self, text: str, type: str = "json") -> str:
        start = f"""```{type}"""
        end = """```"""

        start_idx = text.find(start)
        end_idx = text.rfind(end)

        if start_idx >= 0 and end_idx >= 0:
            start_idx += len(type) + 3
            end_idx -= 1
            return (text[start_idx:end_idx]).strip()

        return text.strip()

    def _complete(self):
        if self.model.startswith("o1"):
            return self.client.chat.completions.create(
                model=self.model, messages=self.messages
            )
        else:
            return self.client.chat.completions.create(
                model=self.model, messages=self.messages, temperature=self.temperature
            )

    def completeAsJSON(self, prompt, append_history: bool = False):
        prompt = f"""
        {prompt}    
        """

        if not append_history:
            self.messages = []

        # Add the user's message to the conversation history
        self.messages.append({"role": "user", "content": prompt})

        # Make a request to the OpenAI API with the entire conversation history
        response = self._complete()

        # Extract the assistant's reply
        reply = response.choices[0].message.content
        output = self.extract_markdown_content(reply, "json")

        # Add the assistant's reply to the conversation history
        self.messages.append({"role": "assistant", "content": reply})

        return output

    def complete(self, user_input, append_history: bool = False):
        if not self.use_chat_history:
            self.messages = []

        # Add the user's message to the conversation history
        self.messages.append({"role": "user", "content": user_input})

        # Make a request to the OpenAI API with the entire conversation history
        response = self._complete()

        # Extract the assistant's reply
        assistant_reply = response.choices[0].message.content

        # Add the assistant's reply to the conversation history
        self.messages.append({"role": "assistant", "content": assistant_reply})

        return assistant_reply

    def display_conversation(self):
        for message in self.messages[1:]:  # Skip the initial system message
            role = message["role"].capitalize()
            content = message["content"]
            print(f"{role}: {content}")


def main():
    from dotenv import load_dotenv
    import os
    load_dotenv(os.getcwd() + "/.env")

    api_key = os.getenv('OPENAI_API_KEY')

    chatbot = OpenAIChatBot(api_key=api_key, model="o1-preview")
    user_input = "tell me a joke"

    response = chatbot.complete(user_input)
    print("ChatGPT-4:", response)

    response = chatbot.completeAsJSON(user_input)
    print("ChatGPT-4:", response)


if __name__ == "__main__":
    main()
