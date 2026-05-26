"""Converter factory for managing file format converters."""

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple, Type

logger = logging.getLogger(__name__)


class BaseConverter:
    """Base class for all file converters."""
    
    def convert(self, file_path: Path) -> Tuple[bool, Optional[str], str]:
        """Convert file to text.
        
        Args:
            file_path: Path to the file to convert
            
        Returns:
            Tuple of (success, text, error_message)
        """
        raise NotImplementedError("Subclasses must implement convert()")


class ConverterFactory:
    """Factory for creating appropriate converter instances.
    
    Features:
    - Prevents duplicate converter registration
    - Logs registration attempts
    - Provides clear error messages
    """

    _converters: Dict[str, Type[BaseConverter]] = {}
    _allow_override: bool = False  # Set to True only during testing

    @classmethod
    def register_converter(
        cls, 
        extension: str, 
        converter_class: Type[BaseConverter],
        override: bool = False
    ) -> None:
        """Register a converter for a file extension.
        
        Args:
            extension: File extension (e.g., '.pdf', '.xlsx')
            converter_class: Converter class to register
            override: Whether to override existing registration
            
        Raises:
            ValueError: If converter already registered and override=False
        """
        ext_lower = extension.lower()
        
        # Check for duplicate registration
        if ext_lower in cls._converters and not (override or cls._allow_override):
            existing = cls._converters[ext_lower].__name__
            new = converter_class.__name__
            if existing != new:
                logger.warning(
                    f"Attempted to register {new} for {ext_lower}, "
                    f"but {existing} is already registered"
                )
                raise ValueError(
                    f"Converter already registered for {ext_lower}: {existing}"
                )
            else:
                # Same converter being re-registered, silently ignore
                return
        
        cls._converters[ext_lower] = converter_class
        logger.debug(f"Registered {converter_class.__name__} for {ext_lower}")

    @classmethod
    def unregister_converter(cls, extension: str) -> None:
        """Unregister a converter (mainly for testing).
        
        Args:
            extension: File extension to unregister
        """
        ext_lower = extension.lower()
        if ext_lower in cls._converters:
            converter_name = cls._converters[ext_lower].__name__
            del cls._converters[ext_lower]
            logger.debug(f"Unregistered {converter_name} for {ext_lower}")

    @classmethod
    def get_converter(cls, file_path: Path) -> Optional[BaseConverter]:
        """Get appropriate converter for file.

        Args:
            file_path: Path to the file

        Returns:
            Converter instance or None if not supported
        """
        extension = file_path.suffix.lower()
        converter_class = cls._converters.get(extension)

        if converter_class:
            return converter_class()

        return None

    @classmethod
    def is_supported(cls, file_path: Path) -> bool:
        """Check if file format is supported.
        
        Args:
            file_path: Path to check
            
        Returns:
            True if a converter is registered for this file type
        """
        return file_path.suffix.lower() in cls._converters

    @classmethod
    def list_supported_formats(cls) -> list[str]:
        """Get list of supported file extensions.
        
        Returns:
            List of supported extensions
        """
        return sorted(cls._converters.keys())

    @classmethod
    def clear_all(cls) -> None:
        """Clear all registered converters (mainly for testing)."""
        cls._converters.clear()
        logger.debug("Cleared all converter registrations")


def convert_to_text(file_path: Path) -> Tuple[bool, Optional[str], str]:
    """Convert any supported file to text.

    Args:
        file_path: Path to the file

    Returns:
        Tuple of (success, text, error_message)
    """
    try:
        converter = ConverterFactory.get_converter(file_path)
        if not converter:
            return False, None, f"Unsupported file format: {file_path.suffix}"

        return converter.convert(file_path)
    except Exception as e:
        logger.error(f"Conversion error for {file_path}: {e}")
        return False, None, f"Conversion error: {str(e)}"