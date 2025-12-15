"""
Conversational AI service for intelligent query handling.

This service acts as the main entry point for chat-based queries.
It delegates to the QueryOrchestrator for the actual processing pipeline.

Architecture:
- ConversationService: Entry point, maintains backward compatibility
- QueryOrchestrator: Coordinates the two-phase LLM pipeline
- Phase 1: Query generation (LLM creates query structure only)
- Phase 2: Response generation (LLM creates message from actual data)
"""

import logging
from typing import Dict, Any, List, Optional

from .query_orchestrator import get_orchestrator, reload_orchestrator

logger = logging.getLogger(__name__)


class ConversationService:
    """
    Handles conversational interactions for data queries.
    
    This is a thin wrapper that delegates to QueryOrchestrator
    while maintaining backward compatibility with existing API.
    """
    
    def __init__(self):
        """Initialize the conversation service."""
        self._orchestrator = get_orchestrator()
        self.index_schemas = self._orchestrator.schema_cache
    
    def process_message(
        self,
        user_message: str,
        conversation_history: List[Dict] = None,
        session_id: str = "default"
    ) -> Dict[str, Any]:
        """
        Process a user message and return an intelligent response.
        
        Args:
            user_message: Natural language query from user
            conversation_history: Previous messages for context
            session_id: Session identifier for context tracking
            
        Returns:
            Response dict with query results and formatted message
        """
        if conversation_history is None:
            conversation_history = []
        
        try:
            # Delegate to orchestrator
            result = self._orchestrator.process(
                user_message=user_message,
                conversation_history=conversation_history,
                session_id=session_id
            )
            
            return result
            
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            return {
                'type': 'error',
                'message': 'Sorry, I encountered an error processing your request. Please try again.',
                'error': str(e)
            }


# Singleton instance
_conversation_service: Optional[ConversationService] = None


def get_conversation_service() -> ConversationService:
    """Get the singleton conversation service instance."""
    global _conversation_service
    if _conversation_service is None:
        _conversation_service = ConversationService()
    return _conversation_service


def reload_schemas() -> Dict[str, Any]:
    """
    Force reload all schemas - useful when data changes.
    
    Returns:
        Status dict with reload information
    """
    global _conversation_service
    
    # Reload the orchestrator
    orchestrator = reload_orchestrator()
    
    # Reset conversation service
    _conversation_service = None
    service = get_conversation_service()
    
    return {
        'status': 'reloaded',
        'indexes': list(service.index_schemas.keys()),
        'field_counts': {
            k: v.get('field_count', 0) 
            for k, v in service.index_schemas.items()
        },
        'document_counts': {
            k: v.get('document_count', 0) 
            for k, v in service.index_schemas.items()
        }
    }
