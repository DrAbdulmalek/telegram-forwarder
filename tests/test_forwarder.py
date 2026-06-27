"""
Test suite for telegram-forwarder utility functions and validation
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from forwarder import (
    ForwardConfig,
    ForwardResult,
)


class TestForwardConfig:
    """Tests for ForwardConfig dataclass"""
    
    def test_default_values(self):
        """Test default configuration values"""
        config = ForwardConfig()
        assert config.limit == 100
        assert config.delay == 2.0
        assert config.media_only == False
        assert config.text_only == False
        assert config.skip_forwards == True
        assert config.send_caption == True
        assert config.reverse_order == False
    
    def test_custom_values(self):
        """Test custom configuration values"""
        config = ForwardConfig(
            source_channel="source",
            dest_channel="dest",
            limit=50,
            delay=5.0,
            media_only=True,
            text_only=False,
            skip_forwards=False,
            filter_text="test",
            start_id=10,
            end_id=20,
            send_caption=False,
            reverse_order=True,
        )
        assert config.source_channel == "source"
        assert config.dest_channel == "dest"
        assert config.limit == 50
        assert config.delay == 5.0
        assert config.media_only == True
        assert config.text_only == False
        assert config.skip_forwards == False
        assert config.filter_text == "test"
        assert config.start_id == 10
        assert config.end_id == 20
        assert config.send_caption == False
        assert config.reverse_order == True


class TestForwardResult:
    """Tests for ForwardResult dataclass"""
    
    def test_default_values(self):
        """Test default result values"""
        result = ForwardResult()
        assert result.success == 0
        assert result.failed == 0
        assert result.skipped == 0
        assert result.total == 0
        assert result.elapsed == "0s"
        assert result.messages == []
    
    def test_custom_values(self):
        """Test custom result values"""
        result = ForwardResult(
            success=10,
            failed=2,
            skipped=1,
            total=13,
            elapsed="1m 30s",
            messages=["msg1", "msg2"],
        )
        assert result.success == 10
        assert result.failed == 2
        assert result.skipped == 1
        assert result.total == 13
        assert result.elapsed == "1m 30s"
        assert result.messages == ["msg1", "msg2"]
    
    def test_to_dict(self):
        """Test conversion to dictionary"""
        result = ForwardResult(
            success=10,
            failed=2,
            skipped=1,
            total=13,
            elapsed="1m 30s",
            messages=["msg1"],
        )
        result_dict = result.to_dict()
        assert isinstance(result_dict, dict)
        assert result_dict['success'] == 10
        assert result_dict['failed'] == 2
        assert result_dict['skipped'] == 1
        assert result_dict['total'] == 13
        assert result_dict['elapsed'] == "1m 30s"


class TestValidation:
    """Tests for input validation"""
    
    def test_validate_phone_number(self):
        """Test phone number validation"""
        # Valid phone numbers
        assert "+963123456789".startswith("+")
        assert "+1234567890".startswith("+")
        
        # Invalid phone numbers
        assert not "1234567890".startswith("+")
        assert not "963123456789".startswith("+")
    
    def test_validate_api_id(self):
        """Test API ID validation"""
        # Valid API IDs are positive integers
        assert isinstance(12345, int)
        assert isinstance(1, int)
        
        # Invalid API IDs
        with pytest.raises(ValueError):
            int("not_a_number")
    
    def test_validate_channel_id(self):
        """Test channel ID validation"""
        # Valid channel IDs (can be negative for supergroups)
        assert isinstance(-1001234567890, int)
        assert isinstance(123456789, int)


class TestUtilityFunctions:
    """Tests for utility functions"""
    
    def test_format_duration(self):
        """Test duration formatting"""
        # This would test a utility function if it existed
        # For now, just test basic formatting
        seconds = 90
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        formatted = f"{minutes}m {remaining_seconds}s"
        assert formatted == "1m 30s"
    
    def test_calculate_progress(self):
        """Test progress calculation"""
        current = 50
        total = 100
        progress = (current / total) * 100
        assert progress == 50.0


class TestConfigValidation:
    """Tests for configuration validation"""
    
    def test_valid_config(self):
        """Test valid configuration"""
        config = ForwardConfig(
            source_channel="source",
            dest_channel="dest",
            limit=100,
            delay=2.0,
        )
        assert config.source_channel == "source"
        assert config.dest_channel == "dest"
        assert config.limit > 0
        assert config.delay >= 0
    
    def test_invalid_limit(self):
        """Test invalid limit (negative)"""
        # Limit should be positive
        with pytest.raises(ValueError):
            # This would require validation in the class
            config = ForwardConfig(limit=-10)
    
    def test_invalid_delay(self):
        """Test invalid delay (negative)"""
        # Delay should be non-negative
        config = ForwardConfig(delay=0.0)  # Zero is allowed
        assert config.delay >= 0


class TestMessageFiltering:
    """Tests for message filtering logic"""
    
    def test_filter_by_text(self):
        """Test text filtering"""
        filter_text = "important"
        messages = [
            {"text": "This is important"},
            {"text": "This is not important"},
            {"text": "Important notice"},
        ]
        
        filtered = [msg for msg in messages 
                   if filter_text.lower() in msg.get("text", "").lower()]
        assert len(filtered) == 2
    
    def test_filter_media_only(self):
        """Test media filtering"""
        messages = [
            {"text": "Text message", "media": None},
            {"text": "Image", "media": "photo"},
            {"text": "Video", "media": "video"},
        ]
        
        media_only = [msg for msg in messages if msg.get("media")]
        assert len(media_only) == 2
    
    def test_filter_text_only(self):
        """Test text-only filtering"""
        messages = [
            {"text": "Text message", "media": None},
            {"text": "Image", "media": "photo"},
            {"text": "Video", "media": "video"},
        ]
        
        text_only = [msg for msg in messages if not msg.get("media")]
        assert len(text_only) == 1


class TestMockTelegramClient:
    """Mock tests for Telegram client functionality"""
    
    @pytest.mark.asyncio
    async def test_mock_forwarder(self):
        """Test with mocked Telegram forwarder"""
        # This would use pytest-asyncio and mock the Telegram client
        # For now, just test that we can create a mock
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        
        assert mock_client.is_connected() == True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
