from datetime import datetime, timezone, time, date, timedelta
from typing import Dict, Any, Tuple, Optional, Union
from zoneinfo import ZoneInfo
from loguru import logger

# ==========================================
# CENTRALIZED TIMEZONE SINGLE SOURCE OF TRUTH
# ==========================================
US_EASTERN_TZ = ZoneInfo("America/New_York")
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
UTC_TZ = timezone.utc

# Backward-compatible GMT+3 (Istanbul) session constants
MARKET_TIMEZONE: str = "GMT+3"
MARKET_OPEN_TIME: str = "16:30"
MARKET_CLOSE_TIME: str = "23:00"

PRE_MARKET_OPEN_TIME: str = "12:00"
PRE_MARKET_CLOSE_TIME: str = "16:30"
NORMAL_MARKET_OPEN_TIME: str = "16:30"
NORMAL_MARKET_CLOSE_TIME: str = "23:00"
AFTER_MARKET_OPEN_TIME: str = "23:00"
AFTER_MARKET_CLOSE_TIME: str = "03:00"
MARKET_CLOSED_OPEN_TIME: str = "03:00"
MARKET_CLOSED_CLOSE_TIME: str = "12:00"

# Timezone object for GMT+3 (UTC + 3 hours, Istanbul)
SESSION_TZ = timezone(timedelta(hours=3), name="GMT+3")
SESSION_OPEN_TIME = time(16, 30, 0)
SESSION_CLOSE_TIME = time(23, 0, 0)

# US Regular Session Hours in Eastern Time
US_MARKET_OPEN_TIME = time(9, 30, 0)
US_MARKET_CLOSE_TIME = time(16, 0, 0)
TRADING_MINUTES_PER_DAY = 390  # 6.5 hours = 390 minutes


class SessionName(str):
    """
    Smart string representation of session names.
    Supports standard names ('NORMAL_MARKET', 'PRE_MARKET', 'AFTER_MARKET', 'CLOSED')
    while providing backward-compatible equality with legacy names.
    """
    def __eq__(self, other: Any) -> bool:
        if super().__eq__(other):
            return True
        val = str(self)
        if val == "NORMAL_MARKET" and other in ("REGULAR_HOURS", "SESSION_OPEN"):
            return True
        if val == "PRE_MARKET" and other in ("PRE_SESSION",):
            return True
        if val == "AFTER_MARKET" and other in ("AFTER_HOURS", "POST_SESSION"):
            return True
        if val in ("CLOSED", "MARKET_CLOSED", "WEEKEND_CLOSED") and other in ("CLOSED", "MARKET_CLOSED", "WEEKEND_CLOSED"):
            return True
        return False


class MarketSchedule:
    """
    Centralized market hours, session detector, and position timestamp formatter.
    Enforces the GMT+3 (Istanbul) session timeline:
      - PRE MARKET:    12:00 - 16:30 GMT+3
      - NORMAL MARKET: 16:30 - 23:00 GMT+3 (Positions may ONLY be opened here)
      - AFTER MARKET:  23:00 - 03:00 GMT+3
      - MARKET CLOSED: 03:00 - 12:00 GMT+3 (and weekends)

    At exactly 23:00:00 GMT+3 (or anytime outside 16:30 - 23:00), all open positions must be closed.
    """

    TIMEZONE_NAME = MARKET_TIMEZONE
    OPEN_TIME_STR = MARKET_OPEN_TIME
    CLOSE_TIME_STR = MARKET_CLOSE_TIME
    TZ = SESSION_TZ
    OPEN_TIME = SESSION_OPEN_TIME
    CLOSE_TIME = SESSION_CLOSE_TIME
    US_TZ = US_EASTERN_TZ
    TR_TZ = ISTANBUL_TZ

    @classmethod
    def get_current_session_time(cls, dt: Optional[datetime] = None) -> datetime:
        """Convert UTC or local datetime to GMT+3 (session timezone)."""
        if dt is None:
            dt = datetime.now(timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(cls.TZ)

    @classmethod
    def get_us_market_time(cls, dt: Optional[datetime] = None) -> datetime:
        """Convert UTC or local datetime to America/New_York (US Eastern Time)."""
        if dt is None:
            dt = datetime.now(timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(cls.US_TZ)

    @classmethod
    def get_istanbul_time(cls, dt: Optional[datetime] = None) -> datetime:
        """Convert UTC or local datetime to Europe/Istanbul (TRT)."""
        if dt is None:
            dt = datetime.now(timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(cls.TR_TZ)

    @classmethod
    def get_utc_time(cls, dt: Optional[datetime] = None) -> datetime:
        """Convert any datetime to timezone-aware UTC."""
        if dt is None:
            return datetime.now(timezone.utc)
        elif dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @classmethod
    def get_market_close_timestamp(cls, dt: Optional[datetime] = None) -> datetime:
        """Return today's 16:00:00 ET regular market close timestamp in America/New_York."""
        et_dt = cls.get_us_market_time(dt)
        return et_dt.replace(hour=16, minute=0, second=0, microsecond=0)

    @classmethod
    def get_expiration_timestamp(cls, expiration_date_str: str, dt: Optional[datetime] = None) -> datetime:
        """
        Return the 16:00:00 ET expiration timestamp for a given YYYY-MM-DD expiration date.
        """
        et_dt = cls.get_us_market_time(dt)
        if not expiration_date_str or expiration_date_str in ("N/A", "Data unavailable"):
            return cls.get_market_close_timestamp(dt)
        try:
            exp_d = datetime.strptime(str(expiration_date_str)[:10], "%Y-%m-%d").date()
            return datetime(exp_d.year, exp_d.month, exp_d.day, 16, 0, 0, tzinfo=cls.US_TZ)
        except Exception:
            return cls.get_market_close_timestamp(dt)

    @classmethod
    def get_minutes_to_market_close(cls, dt: Optional[datetime] = None) -> float:
        """
        Calculate minutes remaining until US regular session close (16:00 ET).
        If before 09:30 ET on a weekday, returns full session (390.0 minutes).
        If during regular session (09:30 - 16:00 ET), returns actual minutes remaining.
        If after 16:00 ET or weekend, returns 0.0 minutes.
        """
        et_dt = cls.get_us_market_time(dt)
        weekday = et_dt.weekday()
        if weekday >= 5:
            return 0.0

        t = et_dt.time()
        close_dt = cls.get_market_close_timestamp(et_dt)
        open_dt = et_dt.replace(hour=9, minute=30, second=0, microsecond=0)

        if t < time(9, 30):
            return float(TRADING_MINUTES_PER_DAY)
        elif t >= time(16, 0):
            return 0.0
        else:
            diff_sec = max(0.0, (close_dt - et_dt).total_seconds())
            return round(diff_sec / 60.0, 2)

    @classmethod
    def get_session_progress(cls, dt: Optional[datetime] = None) -> float:
        """
        Calculate fraction of the regular US trading session elapsed (0.0 to 1.0).
        0.0 = At or before 09:30 ET
        1.0 = At or after 16:00 ET (or weekend)
        """
        et_dt = cls.get_us_market_time(dt)
        weekday = et_dt.weekday()
        if weekday >= 5:
            return 1.0

        t = et_dt.time()
        if t < time(9, 30):
            return 0.0
        elif t >= time(16, 0):
            return 1.0

        open_dt = et_dt.replace(hour=9, minute=30, second=0, microsecond=0)
        elapsed_sec = max(0.0, (et_dt - open_dt).total_seconds())
        progress = elapsed_sec / (TRADING_MINUTES_PER_DAY * 60.0)
        return min(1.0, max(0.0, round(progress, 4)))

    @classmethod
    def get_fraction_of_trading_day_remaining(cls, dt: Optional[datetime] = None) -> float:
        """
        Calculate fraction of the regular trading session remaining (1.0 down to 0.0).
        """
        progress = cls.get_session_progress(dt)
        return round(max(0.0, 1.0 - progress), 4)

    @classmethod
    def get_time_of_day_bucket(cls, dt: Optional[datetime] = None) -> str:
        """
        Classify the current time into intraday market regimes:
        - OPEN: 09:30 - 10:00 ET (opening range discovery, wide spreads, high volatility)
        - MORNING: 10:00 - 11:30 ET (prime trend expansion window)
        - MIDDAY: 11:30 - 14:00 ET (lunch lull, chop / mean reversion risk)
        - AFTERNOON: 14:00 - 15:30 ET (institutional continuation / rotation)
        - EXPIRATION_WINDOW: 15:30 - 16:00 ET (0DTE extreme gamma / theta collapse, no entries)
        - PRE_MARKET: 04:00 - 09:30 ET
        - AFTER_HOURS: 16:00 - 20:00 ET
        - CLOSED: 20:00 - 04:00 ET and weekends
        """
        et_dt = cls.get_us_market_time(dt)
        weekday = et_dt.weekday()
        if weekday >= 5:
            return "CLOSED"

        t = et_dt.time()
        if time(9, 30) <= t < time(10, 0):
            return "OPEN"
        elif time(10, 0) <= t < time(11, 30):
            return "MORNING"
        elif time(11, 30) <= t < time(14, 0):
            return "MIDDAY"
        elif time(14, 0) <= t < time(15, 30):
            return "AFTERNOON"
        elif time(15, 30) <= t < time(16, 0):
            return "EXPIRATION_WINDOW"
        elif time(4, 0) <= t < time(9, 30):
            return "PRE_MARKET"
        elif time(16, 0) <= t < time(20, 0):
            return "AFTER_HOURS"
        else:
            return "CLOSED"

    @classmethod
    def calculate_0dte_metrics(
        cls,
        expiration_date_str: Optional[str] = None,
        dt: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Comprehensive 0DTE time-to-expiration and session state evaluator.
        Provides continuous metrics for Greek estimation, theta decay, and gamma acceleration.
        """
        et_dt = cls.get_us_market_time(dt)
        tr_dt = cls.get_istanbul_time(dt)
        utc_dt = cls.get_utc_time(dt)

        exp_dt = cls.get_expiration_timestamp(expiration_date_str or "", dt)
        today_et = et_dt.date()
        exp_date = exp_dt.date()
        dte = max(0, (exp_date - today_et).days)
        is_0dte = (dte == 0)

        # Minutes to expiry
        if is_0dte:
            minutes_to_exp = cls.get_minutes_to_market_close(dt)
            # fraction in annual trading days (252 days * 390 minutes)
            annual_trading_minutes = 252.0 * TRADING_MINUTES_PER_DAY
            # Enforce continuous time (minimum 1 minute to avoid divide-by-zero at closing bell)
            eff_minutes = max(minutes_to_exp, 1.0)
            time_to_expiry_years = eff_minutes / annual_trading_minutes
            fraction_remaining = cls.get_fraction_of_trading_day_remaining(dt)
            session_progress = cls.get_session_progress(dt)
        else:
            fraction_remaining = cls.get_fraction_of_trading_day_remaining(dt)
            session_progress = cls.get_session_progress(dt)
            # For multi-day, include fraction of remaining today plus remaining full days
            eff_days = dte + fraction_remaining
            time_to_expiry_years = max(eff_days / 365.0, 1.0 / 365.0)
            minutes_to_exp = (dte * TRADING_MINUTES_PER_DAY) + (fraction_remaining * TRADING_MINUTES_PER_DAY)

        bucket = cls.get_time_of_day_bucket(dt)

        return {
            "is_0dte": is_0dte,
            "dte": dte,
            "current_time_et": et_dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "current_time_istanbul": tr_dt.strftime("%Y-%m-%d %H:%M:%S TRT"),
            "current_time_utc": utc_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "market_close_timestamp": cls.get_market_close_timestamp(dt).isoformat(),
            "expiration_timestamp": exp_dt.isoformat(),
            "minutes_to_expiry": round(minutes_to_exp, 1),
            "hours_to_expiry": round(minutes_to_exp / 60.0, 2),
            "time_to_expiry_years": round(time_to_expiry_years, 6),
            "session_progress": session_progress,
            "session_progress_pct": f"{round(session_progress * 100, 1)}%",
            "fraction_of_trading_day_remaining": fraction_remaining,
            "time_of_day_bucket": bucket,
            "trading_minutes_per_day": TRADING_MINUTES_PER_DAY
        }

    @classmethod
    def is_market_open(
        cls,
        symbol: Optional[str] = None,
        dt: Optional[datetime] = None
    ) -> Tuple[bool, str, str]:
        """
        Check if the trading session is currently OPEN for positions.
        Session timeline in GMT+3 (Istanbul):
          - PRE MARKET:    12:00 - 16:30
          - NORMAL MARKET: 16:30 - 23:00 (Allowed for opening positions)
          - AFTER MARKET:  23:00 - 03:00
          - MARKET CLOSED: 03:00 - 12:00

        Allowed: 16:30:00 <= current_time < 23:00:00 GMT+3 on weekdays (Mon-Fri).
        Forbidden: anytime outside 16:30:00 - 22:59:59 GMT+3, or weekends.

        Returns:
            (is_open: bool, session_name: SessionName, reason: str)
            session_name: "NORMAL_MARKET" | "PRE_MARKET" | "AFTER_MARKET" | "CLOSED"
        """
        s_dt = cls.get_current_session_time(dt)
        weekday = s_dt.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday
        t = s_dt.time()

        # Handle Borsa Istanbul (.IS) if requested specifically
        if symbol and symbol.upper().endswith(".IS"):
            if weekday >= 5:
                return (
                    False,
                    SessionName("CLOSED"),
                    f"Borsa Istanbul (.IS) is closed on weekends. Regular hours: 10:00 - 18:00 TRT ({cls.TIMEZONE_NAME})."
                )
            if time(10, 0) <= t < time(18, 0):
                return (
                    True,
                    SessionName("REGULAR_HOURS"),
                    f"Borsa Istanbul (.IS) is OPEN (10:00 - 18:00 TRT)."
                )
            return (
                False,
                SessionName("CLOSED"),
                f"Borsa Istanbul (.IS) is CLOSED (10:00 - 18:00 TRT)."
            )

        # 1. Weekend check
        if weekday == 5:  # Saturday
            if t < time(3, 0):
                # Friday night's after-market continues until Saturday 03:00
                return (
                    False,
                    SessionName("AFTER_MARKET"),
                    f"Market is in After-Hours / AFTER MARKET (23:00 - 03:00 {cls.TIMEZONE_NAME}). Normal market ended at 23:00 {cls.TIMEZONE_NAME}."
                )
            return (
                False,
                SessionName("CLOSED"),
                f"Trading session is closed on Saturday. Allowed session: {cls.OPEN_TIME_STR} - {cls.CLOSE_TIME_STR} {cls.TIMEZONE_NAME} (Mon-Fri)."
            )
        elif weekday == 6:  # Sunday
            return (
                False,
                SessionName("CLOSED"),
                f"Trading session is closed on Sunday. Allowed session: {cls.OPEN_TIME_STR} - {cls.CLOSE_TIME_STR} {cls.TIMEZONE_NAME} (Mon-Fri)."
            )

        # 2. Monday early morning check (Sunday night has no US after-market)
        if weekday == 0 and t < time(3, 0):
            return (
                False,
                SessionName("CLOSED"),
                f"Market is CLOSED (03:00 - 12:00 {cls.TIMEZONE_NAME}). Pre-market opens at 12:00 {cls.TIMEZONE_NAME}, Normal market opens at 16:30 {cls.TIMEZONE_NAME}."
            )

        # 3. Weekday timeline (Mon - Fri)
        # NORMAL MARKET: 16:30 - 23:00
        if time(16, 30) <= t < time(23, 0):
            return (
                True,
                SessionName("NORMAL_MARKET"),
                f"Trading session is OPEN (NORMAL MARKET {cls.OPEN_TIME_STR} - {cls.CLOSE_TIME_STR} {cls.TIMEZONE_NAME})."
            )

        # PRE MARKET: 12:00 - 16:30
        elif time(12, 0) <= t < time(16, 30):
            return (
                False,
                SessionName("PRE_MARKET"),
                f"Market is in Pre-Market (12:00 - 16:30 {cls.TIMEZONE_NAME}). Current time is {s_dt.strftime('%H:%M:%S')} {cls.TIMEZONE_NAME}. Allowed normal session: {cls.OPEN_TIME_STR} - {cls.CLOSE_TIME_STR} {cls.TIMEZONE_NAME}."
            )

        # AFTER MARKET: 23:00 - 03:00
        elif t >= time(23, 0) or t < time(3, 0):
            return (
                False,
                SessionName("AFTER_MARKET"),
                f"Market is in After-Hours / AFTER MARKET (23:00 - 03:00 {cls.TIMEZONE_NAME}). Session ended at {cls.CLOSE_TIME_STR} {cls.TIMEZONE_NAME}. Current time is {s_dt.strftime('%H:%M:%S')} {cls.TIMEZONE_NAME}."
            )

        # MARKET CLOSED: 03:00 - 12:00
        else:
            return (
                False,
                SessionName("CLOSED"),
                f"Market is CLOSED (03:00 - 12:00 {cls.TIMEZONE_NAME}). Current time is {s_dt.strftime('%H:%M:%S')} {cls.TIMEZONE_NAME}. Pre-market opens at 12:00 {cls.TIMEZONE_NAME}, Normal market opens at 16:30 {cls.TIMEZONE_NAME}."
            )

    @classmethod
    def is_market_closed(
        cls,
        symbol: Optional[str] = None,
        dt: Optional[datetime] = None
    ) -> bool:
        """Centralized check if market session is closed. Returns True if NOT open."""
        is_open, _, _ = cls.is_market_open(symbol=symbol, dt=dt)
        return not is_open

    @classmethod
    def can_open_position(
        cls,
        symbol: Optional[str] = None,
        dt: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """
        Explicit check if a position may be opened.
        Positions may ONLY be opened during NORMAL MARKET (16:30:00 -> 22:59:59 GMT+3).
        At 23:00:00 GMT+3, opening new positions is completely forbidden.
        """
        is_open, session, reason = cls.is_market_open(symbol=symbol, dt=dt)
        return is_open, reason

    @classmethod
    def get_market_status(
        cls,
        symbol: Optional[str] = None,
        dt: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Provide structured market schedule data with human-readable timestamps and countdowns."""
        utc_dt = dt or datetime.now(timezone.utc)
        if utc_dt.tzinfo is None:
            utc_dt = utc_dt.replace(tzinfo=timezone.utc)

        s_dt = cls.get_current_session_time(utc_dt)
        is_open, session, reason = cls.is_market_open(symbol, utc_dt)

        t = s_dt.time()
        weekday = s_dt.weekday()

        # Session Label
        if is_open:
            session_label = "Normal Market (Open)"
        elif session == "PRE_MARKET":
            session_label = "Pre-Market"
        elif session == "AFTER_MARKET":
            session_label = "After-Market"
        elif weekday >= 5 or (weekday == 5 and t >= time(3, 0)):
            session_label = "Weekend Closed"
        else:
            session_label = "Market Closed"

        # Calculate countdown
        countdown_str = ""
        if weekday >= 5 or (weekday == 5 and t >= time(3, 0)):
            days_ahead = (7 - weekday) % 7
            if days_ahead == 0:
                days_ahead = 1
            next_open = (s_dt + timedelta(days=days_ahead)).replace(
                hour=cls.OPEN_TIME.hour, minute=cls.OPEN_TIME.minute, second=0, microsecond=0
            )
            diff = max(0, int((next_open - s_dt).total_seconds()))
            h, rem = divmod(diff, 3600)
            m, _ = divmod(rem, 60)
            countdown_str = f"Opens Mon in {h}h {m}m"
        elif is_open:
            close_dt = s_dt.replace(
                hour=cls.CLOSE_TIME.hour, minute=cls.CLOSE_TIME.minute, second=0, microsecond=0
            )
            diff = max(0, int((close_dt - s_dt).total_seconds()))
            h, rem = divmod(diff, 3600)
            mins, s = divmod(rem, 60)
            if h > 0:
                countdown_str = f"Closes in {h}h {mins}m {s:02d}s"
            else:
                countdown_str = f"Closes in {mins}m {s:02d}s"
        elif session == "PRE_MARKET":
            open_dt = s_dt.replace(
                hour=cls.OPEN_TIME.hour, minute=cls.OPEN_TIME.minute, second=0, microsecond=0
            )
            diff = max(0, int((open_dt - s_dt).total_seconds()))
            h, rem = divmod(diff, 3600)
            mins, s = divmod(rem, 60)
            if h > 0:
                countdown_str = f"Normal opens in {h}h {mins}m {s:02d}s"
            else:
                countdown_str = f"Normal opens in {mins}m {s:02d}s"
        elif session == "AFTER_MARKET":
            countdown_str = f"Normal opens tomorrow {cls.OPEN_TIME_STR} {cls.TIMEZONE_NAME}"
        else:
            # CLOSED (03:00 - 12:00)
            pre_dt = s_dt.replace(hour=12, minute=0, second=0, microsecond=0)
            diff = max(0, int((pre_dt - s_dt).total_seconds()))
            h, rem = divmod(diff, 3600)
            mins, _ = divmod(rem, 60)
            countdown_str = f"Pre-Market in {h}h {mins}m"

        return {
            "symbol": (symbol or "MARKET").upper(),
            "is_open": is_open,
            "session": str(session),
            "session_label": session_label,
            "reason": reason,
            "can_open_position": is_open,
            "current_time_session": s_dt.strftime(f"%H:%M:%S {cls.TIMEZONE_NAME}"),
            "current_time_gmt3": s_dt.strftime("%H:%M:%S GMT+3"),
            "current_time_gmt2": s_dt.strftime("%H:%M:%S GMT+3"),  # backwards compat
            "current_time_et": s_dt.strftime(f"%H:%M:%S {cls.TIMEZONE_NAME}"),
            "current_time_utc": utc_dt.strftime("%H:%M:%S UTC"),
            "date": s_dt.strftime("%b %d, %Y"),
            "tz_label": cls.TIMEZONE_NAME,
            "countdown": countdown_str,
            "regular_hours": f"{cls.OPEN_TIME_STR} - {cls.CLOSE_TIME_STR} {cls.TIMEZONE_NAME}",
            "schedule": {
                "pre_market": "12:00 - 16:30 GMT+3",
                "normal_market": "16:30 - 23:00 GMT+3",
                "after_market": "23:00 - 03:00 GMT+3",
                "market_closed": "03:00 - 12:00 GMT+3"
            }
        }

    @classmethod
    def format_position_timestamp(
        cls,
        dt: Optional[Union[datetime, str]],
        closed_at: Optional[Union[datetime, str]] = None
    ) -> Dict[str, Any]:
        """
        Produce clear, human-readable position timing including GMT+3 and UTC.
        Calculates elapsed duration for open positions or total holding duration for closed positions.
        """
        if dt is None or dt == "":
            return {
                "time_session": "—",
                "time_gmt3": "—",
                "time_gmt2": "—",
                "time_et": "—",
                "time_utc": "—",
                "date": "—",
                "display": "—",
                "full_display": "—",
                "duration_str": "—",
                "duration_seconds": 0,
                "is_closed": False
            }

        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except Exception:
                return {
                    "time_session": str(dt),
                    "time_gmt3": str(dt),
                    "time_gmt2": str(dt),
                    "time_et": str(dt),
                    "time_utc": str(dt),
                    "date": "—",
                    "display": str(dt),
                    "full_display": str(dt),
                    "duration_str": "—",
                    "duration_seconds": 0,
                    "is_closed": False
                }

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        s_dt = dt.astimezone(cls.TZ)
        utc_dt = dt.astimezone(timezone.utc)

        if isinstance(closed_at, str):
            try:
                closed_at = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            except Exception:
                closed_at = None

        end_dt = closed_at if closed_at is not None else datetime.now(timezone.utc)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

        diff_seconds = max(0, int((end_dt - dt).total_seconds()))
        hours, rem = divmod(diff_seconds, 3600)
        minutes, seconds = divmod(rem, 60)

        if hours > 24:
            days, h = divmod(hours, 24)
            dur_str = f"{days}d {h}h"
        elif hours > 0:
            dur_str = f"{hours}h {minutes}m"
        elif minutes > 0:
            dur_str = f"{minutes}m {seconds}s"
        else:
            dur_str = f"{seconds}s"

        time_gmt3 = s_dt.strftime("%H:%M:%S GMT+3")
        time_utc = utc_dt.strftime("%H:%M:%S UTC")
        date_str = s_dt.strftime("%b %d")

        return {
            "time_session": time_gmt3,
            "time_gmt3": time_gmt3,
            "time_gmt2": time_gmt3,  # backwards compatibility alias
            "time_et": time_gmt3,    # backwards compatibility alias
            "time_utc": time_utc,
            "date": date_str,
            "display": f"{time_gmt3} ({time_utc})",
            "full_display": f"{date_str}, {time_gmt3} ({time_utc})",
            "duration_str": dur_str,
            "duration_seconds": diff_seconds,
            "is_closed": closed_at is not None
        }


# Centralized top-level helpers
def is_market_open(symbol: Optional[str] = None, dt: Optional[datetime] = None) -> Tuple[bool, str, str]:
    return MarketSchedule.is_market_open(symbol=symbol, dt=dt)

def is_market_closed(symbol: Optional[str] = None, dt: Optional[datetime] = None) -> bool:
    return MarketSchedule.is_market_closed(symbol=symbol, dt=dt)
