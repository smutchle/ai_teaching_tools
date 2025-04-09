import google.generativeai as genai
import json
import PIL.Image

class GoogleChatBot:
    """
    ChatBot class for interacting with Google's Gemini models, including image support.
    """

    def __init__(self, api_key, model_name="gemini-pro"):
        """
        Initializes the GoogleChatBot with an API key and model name.

        Args:
            api_key (str): The Google Gemini API key.
            model_name (str, optional): The name of the Gemini model to use.
                                        Defaults to "gemini-pro".  For multi-modal
                                        use "gemini-pro-vision".
        """
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)
        self.chat = self.model.start_chat()  # Initialize chat session
        self.model_name = model_name

    def complete(self, prompt):
        """
        Sends a prompt to the Gemini model and returns the text response.

        Args:
            prompt (str): The prompt to send to the chatbot.

        Returns:
            str: The text response from the chatbot, or None if there's an error.
        """
        try:
            response = self.chat.send_message(prompt)
            return response.text
        except Exception as e:
            print(f"Error during Google Gemini completion: {e}")
            return None

    def completeAsJSON(self, prompt):
        """
        Sends a prompt to the Gemini model expecting a JSON response.

        Args:
            prompt (str): The prompt to send, designed to elicit a JSON response.

        Returns:
            str: The JSON response from the chatbot as a string, or None if error.
                 It's the caller's responsibility to parse this JSON string.
        """
        # Modify prompt to explicitly request JSON (if necessary for Gemini, may depend on model)
        json_prompt = f"{prompt}\n\nPlease respond with valid JSON."
        try:
            response = self.chat.send_message(json_prompt)
            return self.extract_markdown_content(response.text)
        except Exception as e:
            print(f"Error during Google Gemini JSON completion: {e}")
            return None

    def complete_with_image(self, prompt, image_path):
        """
        Sends a prompt along with an image to the Gemini model and returns the text response.

        Args:
            prompt (str): The prompt to send to the chatbot.
            image_path (str): The path to the image file.

        Returns:
            str: The text response from the chatbot, or None if there's an error.
        """
        try:
            img = PIL.Image.open(image_path)
            response = self.model.generate_content([prompt, img]) # no chat history here.
            response.resolve()
            return response.text
        except Exception as e:
            print(f"Error during Google Gemini image completion: {e}")
            return None

    def complete_with_image_in_chat(self, prompt, image_path):
        """
        Sends a prompt along with an image to the Gemini model within the current chat session and returns the text response.

        Args:
            prompt (str): The prompt to send to the chatbot.
            image_path (str): The path to the image file.

        Returns:
            str: The text response from the chatbot, or None if there's an error.
        """
        try:
            img = PIL.Image.open(image_path)
            response = self.chat.send_message([prompt, img])
            return response.text
        except Exception as e:
            print(f"Error during Google Gemini image completion within chat: {e}")
            return None



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

