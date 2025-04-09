import os
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class YouTubeSearch:
    def __init__(self, api_key):
        self.youtube = build("youtube", "v3", developerKey=api_key)

    def search(self, topic, max_results=10):
        try:
            search_response = (
                self.youtube.search()
                .list(q=topic, type="video", part="id,snippet", maxResults=max_results)
                .execute()
            )

            video_info = []
            for search_result in search_response.get("items", []):
                video_id = search_result["id"]["videoId"]
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                video_title = search_result["snippet"]["title"]
                video_creator = search_result["snippet"]["channelTitle"]
                video_info.append({
                    "url": video_url,
                    "title": video_title,
                    "creator": video_creator
                })

            return video_info

        except HttpError as e:
            print(f"An HTTP error {e.resp.status} occurred:\n{e.content}")
            return []


# Example usage
if __name__ == "__main__":
    api_key = ""
    yt_search = YouTubeSearch(api_key)
    results = yt_search.search("Python programming", max_results=5)
    for video in results:
        print(f"Title: {video['title']}")
        print(f"Creator: {video['creator']}")
        print(f"URL: {video['url']}")
        print()