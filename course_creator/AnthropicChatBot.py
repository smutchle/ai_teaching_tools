import anthropic
import json
import os
import re
import time
from anthropic._exceptions import APIStatusError


class AnthropicChatBot:
    def __init__(
        self,
        api_key,
        model="claude-sonnet-4-0",
        use_chat_history=False,
        temperature=0.7,
        num_retries=10,
        max_tokens=64000
    ):
        self.client = anthropic.Client(api_key=api_key)
        self.use_chat_history = use_chat_history
        self.chat_history = []
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.num_retries = num_retries

    def _make_request(self, messages):
        retries = 0
        while retries <= self.num_retries:
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    messages=messages,
                )
                return response
            except APIStatusError as e:
                if e.status_code == 529 and retries < self.num_retries:
                    retries += 1
                    print(
                        f"Received 529 error. Attempt {retries} of {self.num_retries}. Waiting 10 seconds..."
                    )
                    time.sleep(10)
                else:
                    raise e

    def _process_response(self, response):
        content = response.content[0].text
        content = content.encode("utf-8").decode("utf-8").strip()
        return content

    def complete(self, prompt):
        messages = (
            self.chat_history + [{"role": "user", "content": prompt}]
            if self.use_chat_history
            else [{"role": "user", "content": prompt}]
        )
        response = self._make_request(messages)
        processed_response = self._process_response(response)

        if self.use_chat_history:
            self.chat_history.append({"role": "user", "content": prompt})
            self.chat_history.append(
                {"role": "assistant", "content": processed_response}
            )

        return processed_response

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

    def completeAsJSON(self, prompt):
        # json_prompt = f"""
        # {prompt}    
        #  The output must be in JSON format without any additional output.
        #  The output must be able to be processed as: json.dumps(json.loads(json_string)) where
        # json_string is the output.
        # """
        json_prompt = prompt
        response = self.complete(json_prompt)
        return self.extract_markdown_content(response, "json")


def main():
    from dotenv import load_dotenv
    import os
    load_dotenv(os.getcwd() + "/.env")

    api_key = os.getenv('CLAUDE_API_KEY')

    chatbot = AnthropicChatBot(api_key, use_chat_history=False)

    prompt = "Tell me a joke and be super creative!"

    print("Testing complete function:")
    response = chatbot.complete(prompt)
    print(response)

    print("Testing complete function as JSON:")
    response = chatbot.completeAsJSON(prompt)
    print(response)


if __name__ == "__main__":
    main()
