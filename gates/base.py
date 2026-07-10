from dataclasses import dataclass, field


# Статусы гейта. "fail" — единственный статус, который останавливает цепочку
# (см. run_gates.py). "not_implemented"/"skipped" пропускаются с пометкой,
# чтобы уже сейчас можно было гонять fail-fast на гейтах 01-02, не дожидаясь
# реализации 03-06.
PASS = "pass"
FAIL = "fail"
NOT_IMPLEMENTED = "not_implemented"
NOT_CONFIGURED = "not_configured"  # код есть, но нет кредов/окружения для вызова
SKIPPED = "skipped"


@dataclass
class GateResult:
    status: str
    message: str
    details: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == PASS

    @property
    def blocks(self) -> bool:
        """Останавливает ли цепочку гейтов."""
        return self.status == FAIL
