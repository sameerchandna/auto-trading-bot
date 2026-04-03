"""Generate PineScript indicator with parameters from optimized_params.json.

Reads the template (bot_indicator.pine), injects current strategy params
into the input defaults, and writes the output. This keeps BT and Pine in sync.

Usage:
    python -m tradingview.generate_pine          # overwrites bot_indicator.pine
    python -m tradingview.generate_pine --check   # dry-run, exits 1 if out of sync
"""
import re
import sys
from pathlib import Path

# Allow running as module or script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.params import load_strategy_params

PINE_FILE = Path(__file__).parent / "bot_indicator.pine"

# Map from optimized_params.json keys to PineScript input variable names
PARAM_MAP = {
    # Weights
    "htf_bias": ("w_htf_bias", "input.float"),
    "bos": ("w_bos", "input.float"),
    "wave_position": ("w_wave", "input.float"),
    "liquidity_sweep": ("w_liquidity", "input.float"),
    "sr_reaction": ("w_sr", "input.float"),
    "wave_ending": ("w_wave_end", "input.float"),
}


def _replace_input_default(line: str, var_name: str, new_value) -> str:
    """Replace the default value in a PineScript input.xxx() call."""
    # Match: var_name = input.float(OLD_VALUE, ...
    # or:   var_name = input.int(OLD_VALUE, ...
    pattern = rf'^(\s*{re.escape(var_name)}\s*=\s*input\.(?:float|int)\()([^,]+)(,.*)$'
    m = re.match(pattern, line)
    if m:
        if isinstance(new_value, float):
            formatted = f"{new_value:.3f}".rstrip('0').rstrip('.')
            # Ensure at least one decimal for floats
            if '.' not in formatted:
                formatted += ".0"
        else:
            formatted = str(new_value)
        return f"{m.group(1)}{formatted}{m.group(3)}\n"
    return line


def generate(check_only: bool = False) -> bool:
    """Inject params into pine. Returns True if file was (or would be) changed."""
    params = load_strategy_params()
    content = PINE_FILE.read_text()
    lines = content.splitlines(keepends=True)

    # Ensure we have newlines
    if lines and not lines[0].endswith('\n'):
        lines = [l + '\n' for l in content.splitlines()]

    new_lines = []
    for line in lines:
        stripped = line.lstrip()

        # Weights
        for param_key, (pine_var, _) in PARAM_MAP.items():
            if stripped.startswith(pine_var):
                weight_val = params["weights"].get(param_key)
                if weight_val is not None:
                    line = _replace_input_default(line, pine_var, round(weight_val, 3))
                break

        # Threshold
        if stripped.startswith("confluence_thr"):
            line = _replace_input_default(line, "confluence_thr", round(params["threshold"], 2))

        # SL multiplier
        if stripped.startswith("sl_atr_mult"):
            line = _replace_input_default(line, "sl_atr_mult", round(params["sl_multiplier"], 1))

        # TP RR
        if stripped.startswith("tp_rr"):
            line = _replace_input_default(line, "tp_rr", round(params["tp_risk_reward"], 1))

        # Swing lookback
        if stripped.startswith("swing_lookback"):
            line = _replace_input_default(line, "swing_lookback", int(params["swing_lookback"]))

        new_lines.append(line)

    new_content = "".join(new_lines)
    changed = new_content != content

    if check_only:
        if changed:
            print("Pine script is OUT OF SYNC with optimized_params.json")
            return True
        else:
            print("Pine script is in sync.")
            return False

    if changed:
        PINE_FILE.write_text(new_content)
        print(f"Updated {PINE_FILE.name} with current strategy params.")
    else:
        print(f"{PINE_FILE.name} already in sync.")

    return changed


if __name__ == "__main__":
    check = "--check" in sys.argv
    changed = generate(check_only=check)
    if check and changed:
        sys.exit(1)
