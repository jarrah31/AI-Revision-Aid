from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class SRSUpdate:
    easiness_factor: float
    interval_days: int
    repetitions: int
    next_review_date: date


def sm2_update(
    quality: int,
    current_ef: float = 2.5,
    current_interval: int = 0,
    current_reps: int = 0,
) -> SRSUpdate:
    """SM-2 spaced repetition algorithm.

    Quality ratings:
      5 - perfect response
      4 - correct after hesitation
      3 - correct with serious difficulty
      2 - incorrect; correct answer seemed easy
      1 - incorrect; remembered after seeing answer
      0 - complete blackout
    """
    quality = max(0, min(5, quality))

    # Update easiness factor
    new_ef = current_ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ef = max(1.3, new_ef)

    if quality >= 3:
        # Correct response
        if current_reps == 0:
            new_interval = 1
        elif current_reps == 1:
            new_interval = 6
        else:
            new_interval = round(current_interval * new_ef)
        new_reps = current_reps + 1
    else:
        # Incorrect - reset
        new_interval = 1
        new_reps = 0

    next_date = date.today() + timedelta(days=new_interval)

    return SRSUpdate(
        easiness_factor=round(new_ef, 2),
        interval_days=new_interval,
        repetitions=new_reps,
        next_review_date=next_date,
    )
