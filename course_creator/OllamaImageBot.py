import requests
import base64
import json


class OllamaImageBot:
    def __init__(
        self,
        model: str = "llava",
        end_point_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.end_point_url = end_point_url

    def complete(self, prompt: str, image_file_path: str) -> str:
        # Read and encode the image file
        with open(image_file_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

        # Prepare the payload
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [encoded_image],
            "stream": False,
        }

        # Send the request to the Ollama API
        response = requests.post(f"{self.end_point_url}/api/generate", json=payload)

        if response.status_code == 200:
            return response.json().get("response", "")
        else:
            return f"Error: {response.status_code} - {response.text}"


def main():
    # Create an instance of OllamaImageBot
    bot = OllamaImageBot(model="llava", end_point_url="http://localhost:11434")

    # Specify the image file path and prompt
    image_path = "C:\\Temp\\art1.jpg"
    prompt = "What is this person doing?  Are they touching the art?"

    # Get the response
    response = bot.complete(prompt, image_path)

    # Print the result
    print(f"Prompt: {prompt}")
    print(f"Image: {image_path}")
    print(f"Response: {response}")

    # Specify the image file path and prompt
    image_path = "C:\\Temp\\art2.jpg"
    prompt = "What is this person doing?  Are they touching the art?"

    # Get the response
    response = bot.complete(prompt, image_path)

    # Print the result
    print(f"Prompt: {prompt}")
    print(f"Image: {image_path}")
    print(f"Response: {response}")


if __name__ == "__main__":
    main()
