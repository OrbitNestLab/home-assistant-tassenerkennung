"""Load the pure vision module without a Home Assistant installation."""

from pathlib import Path
import sys
import types

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "tassenerkennung"
package = types.ModuleType("tassenerkennung")
package.__path__ = [str(COMPONENT)]
sys.modules["tassenerkennung"] = package
