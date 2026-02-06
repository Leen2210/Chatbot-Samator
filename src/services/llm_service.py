# src/services/llm_service.py
from openai import OpenAI
import ollama
from typing import List, Dict, Optional
import os
from dotenv import load_dotenv

load_dotenv()

class LLMService:
    """Service for handling LLM API calls (OpenAI or Ollama)"""
    
    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER").lower()
        
        if self.provider == "openai":
            self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        elif self.provider == "ollama":
            self.model = os.getenv("OLLAMA_MODEL")
            self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")
        
        self.temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.7"))
        self.max_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "500"))
    
    def chat(self, user_message: str, system_prompt: Optional[str] = None, conversation_history: Optional[List[Dict]] = None) -> str:
        """
        Send a message to LLM and get a response
        
        Args:
            user_message: The user's message
            system_prompt: Optional system prompt to set context
            conversation_history: Optional list of previous messages
        
        Returns:
            The assistant's response as a string
        """
        if self.provider == "openai":
            return self._chat_openai(user_message, system_prompt, conversation_history)
        elif self.provider == "ollama":
            return self._chat_ollama(user_message, system_prompt, conversation_history)
    
    def _chat_openai(self, user_message: str, system_prompt: Optional[str] = None, conversation_history: Optional[List[Dict]] = None) -> str:
        """OpenAI implementation"""
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        if conversation_history:
            messages.extend(conversation_history)
        
        messages.append({"role": "user", "content": user_message})
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            
            return response.choices[0].message.content
        
        except Exception as e:
            print(f"Error calling OpenAI API: {e}")
            return f"Sorry, I encountered an error: {str(e)}"
    
    def _chat_ollama(self, user_message: str, system_prompt: Optional[str] = None, conversation_history: Optional[List[Dict]] = None) -> str:
        """Ollama implementation"""
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        if conversation_history:
            messages.extend(conversation_history)
        
        messages.append({"role": "user", "content": user_message})
        
        try:
            response = ollama.chat(
                model=self.model,
                messages=messages,
                options={
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens  # Ollama uses num_predict instead of max_tokens
                }
            )
            
            return response['message']['content']
        
        except Exception as e:
            print(f"Error calling Ollama API: {e}")
            return f"Sorry, I encountered an error: {str(e)}"
    
    def chat_stream(self, user_message: str, system_prompt: Optional[str] = None):
        """
        Stream response from LLM (for future use if needed)
        """
        if self.provider == "openai":
            yield from self._chat_stream_openai(user_message, system_prompt)
        elif self.provider == "ollama":
            yield from self._chat_stream_ollama(user_message, system_prompt)
    
    def _chat_stream_openai(self, user_message: str, system_prompt: Optional[str] = None):
        """OpenAI streaming implementation"""
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.append({"role": "user", "content": user_message})
        
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True
            )
            
            for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content
        
        except Exception as e:
            yield f"Error: {str(e)}"
    
    def _chat_stream_ollama(self, user_message: str, system_prompt: Optional[str] = None):
        """Ollama streaming implementation"""
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.append({"role": "user", "content": user_message})
        
        try:
            stream = ollama.chat(
                model=self.model,
                messages=messages,
                stream=True,
                options={
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens
                }
            )
            
            for chunk in stream:
                if 'message' in chunk and 'content' in chunk['message']:
                    yield chunk['message']['content']
        
        except Exception as e:
            yield f"Error: {str(e)}"

# Singleton instance
llm_service = LLMService()