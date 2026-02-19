"""
Event Processor Handler - Processes events from SQS queue.

This handler:
1. Receives events from SQS FIFO queue (batch size = 1)
2. Routes to appropriate processor module
3. Deletes message on success
4. Allows retry on failure (goes to DLQ after 3 attempts)

Supports routing to:
- HubSpot to AWS Partner Central sync
- Company sync
- Note sync
- Contact sync
- Microsoft Partner Center sync
- GCP Partners sync
"""

import json
import logging
import os
from typing import Any, Dict

from common.base_handler import BaseLambdaHandler
from common.events import SyncEvent, EventType

# Configure logger
logger = logging.getLogger(__name__)


class EventProcessorHandler(BaseLambdaHandler):
    """
    Event processor that routes SQS events to appropriate handlers.
    
    Design:
    - Processes one event at a time (batch size = 1)
    - Routes based on event_type and event_source
    - Handles errors gracefully
    - Supports retry with exponential backoff (via SQS)
    """
    
    def _execute(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """
        Process SQS events.
        
        Args:
            event: SQS event with Records
            context: Lambda context
            
        Returns:
            Success response or raises exception for retry
        """
        records = event.get("Records", [])
        
        if not records:
            self.logger.warning("No records in SQS event")
            return self._success_response({"message": "No records to process"})
        
        # Process each record (should only be 1 due to batch size)
        results = []
        
        for record in records:
            try:
                result = self._process_record(record)
                results.append(result)
            except Exception as exc:
                # Log error and re-raise to trigger SQS retry
                self.logger.error(
                    f"Error processing record: {exc}",
                    exc_info=True,
                    extra={
                        "messageId": record.get("messageId"),
                        "receiptHandle": record.get("receiptHandle"),
                    }
                )
                raise  # Trigger retry
        
        return self._success_response({
            "message": "Events processed successfully",
            "processed": len(results),
            "results": results,
        })
    
    def _process_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single SQS record.
        
        Args:
            record: SQS record
            
        Returns:
            Processing result
            
        Raises:
            Exception: If processing fails (triggers retry)
        """
        # Parse event from SQS message
        sync_event = SyncEvent.from_sqs_message(record)
        
        self.logger.info(
            f"Processing event: {sync_event.event_type} for {sync_event.object_type} "
            f"{sync_event.object_id} (attempt {sync_event.attempt_count + 1})"
        )
        
        # Increment attempt count
        sync_event.attempt_count += 1
        
        # Route to appropriate processor
        result = self._route_event(sync_event)
        
        self.logger.info(
            f"Successfully processed event {sync_event.event_id}: {result}"
        )
        
        return {
            "eventId": sync_event.event_id,
            "objectId": sync_event.object_id,
            "eventType": sync_event.event_type,
            "result": result,
        }
    
    def _route_event(self, sync_event: SyncEvent) -> Dict[str, Any]:
        """
        Route event to appropriate processor based on type and source.
        
        Args:
            sync_event: Event to route
            
        Returns:
            Processing result
            
        Raises:
            ValueError: If no processor found for event type
        """
        event_type = sync_event.event_type
        event_source = sync_event.event_source
        
        # Route based on event type and source
        if event_type == EventType.DEAL_CREATION:
            return self._process_deal_creation(sync_event)
        
        elif event_type == EventType.DEAL_PROPERTY_CHANGE:
            return self._process_deal_update(sync_event)
        
        elif event_type == EventType.COMPANY_PROPERTY_CHANGE:
            return self._process_company_update(sync_event)
        
        elif event_type == EventType.CONTACT_PROPERTY_CHANGE:
            return self._process_contact_update(sync_event)
        
        elif event_type == EventType.NOTE_CREATION:
            return self._process_note_creation(sync_event)
        
        else:
            raise ValueError(f"Unsupported event type: {event_type}")
    
    def _process_deal_creation(self, sync_event: SyncEvent) -> Dict[str, Any]:
        """
        Process deal creation event.
        
        This imports the processor module and delegates to it.
        """
        from hubspot_to_aws.processor import process_hubspot_deal_creation
        
        return process_hubspot_deal_creation(
            sync_event=sync_event,
            hubspot_client=self.hubspot_client,
            pc_client=self.pc_client,
            logger=self.logger,
        )
    
    def _process_deal_update(self, sync_event: SyncEvent) -> Dict[str, Any]:
        """Process deal property change event."""
        from hubspot_to_aws.processor import process_hubspot_deal_update
        
        return process_hubspot_deal_update(
            sync_event=sync_event,
            hubspot_client=self.hubspot_client,
            pc_client=self.pc_client,
            logger=self.logger,
        )
    
    def _process_company_update(self, sync_event: SyncEvent) -> Dict[str, Any]:
        """Process company property change event."""
        from company_sync.processor import process_company_update
        
        return process_company_update(
            sync_event=sync_event,
            hubspot_client=self.hubspot_client,
            pc_client=self.pc_client,
            logger=self.logger,
        )
    
    def _process_contact_update(self, sync_event: SyncEvent) -> Dict[str, Any]:
        """Process contact property change event."""
        self.logger.info(f"Contact update processing not yet implemented: {sync_event.object_id}")
        return {"action": "skipped", "reason": "not_implemented"}
    
    def _process_note_creation(self, sync_event: SyncEvent) -> Dict[str, Any]:
        """Process note creation event."""
        from note_sync.processor import process_note_creation
        
        return process_note_creation(
            sync_event=sync_event,
            hubspot_client=self.hubspot_client,
            pc_client=self.pc_client,
            logger=self.logger,
        )


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda entry point for event processor.
    
    Args:
        event: SQS event with Records
        context: Lambda context
        
    Returns:
        Processing result
    """
    handler = EventProcessorHandler()
    return handler.handle(event, context)
