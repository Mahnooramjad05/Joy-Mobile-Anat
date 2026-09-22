import pytest

import sheets
from sources import ScrapedDevice

CONDITIONS = [("Like New", 1.0), ("Intact", 0.85), ("Cracked", 0.60), ("Faulty", 0.20)]
TODAY = "2026-09-20"


def device(manufacturer, model, storage, price):
    return ScrapedDevice(manufacturer=manufacturer, model=model, storage=storage,
                         price=price, currency="ILS", source="ksp",
                         source_url="https://example.test/")


def sheet_rows(*specs, legacy=False):
    """Build a Devices tab: (device_id, manufacturer, model, storage, price, active).

    `price` is the Like New price. Each row stores its own condition price and a
    multiplier of 1.0, which is how the sync writes them since the ILS
    migration. `legacy=True` builds the euro-era layout instead -- one base
    price on every row with the Conditions tab ratio in column H -- for testing
    that a sheet in the old shape is migrated correctly.
    """
    rows = [sheets.HEADER]
    for device_id, manufacturer, model, storage, price, active in specs:
        for condition, multiplier in CONDITIONS:
            row_number = len(rows) + 1
            stored_price = price if legacy else round(price * multiplier)
            stored_multiplier = multiplier if legacy else sheets.FIXED_MULTIPLIER
            rows.append([
                device_id, manufacturer, model, storage, "2023",
                str(stored_price), condition, str(stored_multiplier),
                f"=ROUND(F{row_number}*H{row_number},0)",
                active, "2026-01-01",
            ])
    return rows


BASE_SHEET = sheet_rows(
    ("APL-IP15P-128", "Apple", "iPhone 15 Pro", "128GB", 520, "TRUE"),
    ("SAM-S23-128", "Samsung", "Galaxy S23", "128GB", 250, "TRUE"),
    ("GOO-P8P-128", "Google", "Pixel 8 Pro", "128GB", 300, "TRUE"),
)


class TestNewDevices:
    def test_device_not_in_sheet_is_added(self):
        plan = sheets.plan_changes(
            BASE_SHEET,
            [device("Apple", "iPhone 15 Pro", "128GB", 520),
             device("Samsung", "Galaxy S23", "128GB", 250),
             device("Google", "Pixel 8 Pro", "128GB", 300),
             device("OnePlus", "OnePlus 12", "256GB", 320)],
            CONDITIONS, today=TODAY)
        assert len(plan.new_devices) == 1
        assert plan.new_devices[0][0] == "ONE-ONEPLUS12-256"

    def test_new_device_gets_one_row_per_condition(self):
        new = device("OnePlus", "OnePlus 12", "256GB", 320)
        rows = sheets.build_new_rows("ONE-ONEPLUS12-256", new, CONDITIONS, 62, today=TODAY)
        assert len(rows) == 4
        assert [r[sheets.COL_CONDITION - 1] for r in rows] == \
            ["Like New", "Intact", "Cracked", "Faulty"]

    def test_new_rows_carry_self_referencing_formulas(self):
        new = device("OnePlus", "OnePlus 12", "256GB", 320)
        rows = sheets.build_new_rows("ONE-ONEPLUS12-256", new, CONDITIONS, 62, today=TODAY)
        formulas = [r[sheets.COL_FINAL_PRICE - 1] for r in rows]
        assert formulas == ["=ROUND(F62*H62,0)", "=ROUND(F63*H63,0)",
                            "=ROUND(F64*H64,0)", "=ROUND(F65*H65,0)"]

    def test_new_rows_are_active_and_dated(self):
        new = device("OnePlus", "OnePlus 12", "256GB", 320)
        rows = sheets.build_new_rows("ONE-ONEPLUS12-256", new, CONDITIONS, 62, today=TODAY)
        assert all(r[sheets.COL_ACTIVE - 1] == "TRUE" for r in rows)
        assert all(r[sheets.COL_LAST_UPDATED - 1] == TODAY for r in rows)


class TestNoDuplicates:
    def test_same_device_spelled_differently_is_not_re_added(self):
        plan = sheets.plan_changes(
            BASE_SHEET, [device("apple", "IPHONE  15-pro", "128 GB", 520)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert plan.new_devices == []

    def test_existing_device_id_is_preserved(self):
        # The scraper must never rename APL-IP15P-128 to APL-IPHONE15PRO-128.
        plan = sheets.plan_changes(
            BASE_SHEET, [device("Apple", "iPhone 15 Pro", "128GB", 560)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert {u[1] for u in plan.price_updates} == {"APL-IP15P-128"}

    def test_running_twice_changes_nothing_the_second_time(self):
        scraped = [device("Apple", "iPhone 15 Pro", "128GB", 520),
                   device("Samsung", "Galaxy S23", "128GB", 250),
                   device("Google", "Pixel 8 Pro", "128GB", 300)]
        plan = sheets.plan_changes(BASE_SHEET, scraped, CONDITIONS, today=TODAY)
        assert plan.is_empty
        assert plan.unchanged == 12


class TestPriceUpdates:
    def test_changed_price_updates_all_four_rows(self):
        plan = sheets.plan_changes(
            BASE_SHEET, [device("Apple", "iPhone 15 Pro", "128GB", 560)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert len(plan.price_updates) == 4
        # Each condition moves by its own ratio: 520->560, 442->476, and so on.
        moves = {(old, new) for _row, _id, old, new in plan.price_updates}
        assert (520, 560) in moves
        assert (442, 476) in moves

    def test_unchanged_price_produces_no_update(self):
        plan = sheets.plan_changes(
            BASE_SHEET, [device("Apple", "iPhone 15 Pro", "128GB", 520)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert plan.price_updates == []
        assert plan.unchanged == 4


class TestDeactivation:
    def test_device_missing_from_scrape_is_deactivated_not_deleted(self):
        plan = sheets.plan_changes(
            BASE_SHEET,
            [device("Apple", "iPhone 15 Pro", "128GB", 520),
             device("Samsung", "Galaxy S23", "128GB", 250)],
            CONDITIONS, today=TODAY, max_deactivation_ratio=1.0)
        assert {d[1] for d in plan.deactivations} == {"GOO-P8P-128"}

    def test_deactivated_device_returning_is_reactivated(self):
        rows = sheet_rows(
            ("APL-IP15P-128", "Apple", "iPhone 15 Pro", "128GB", 520, "FALSE"),
        )
        plan = sheets.plan_changes(
            rows, [device("Apple", "iPhone 15 Pro", "128GB", 520)],
            CONDITIONS, today=TODAY)
        assert len(plan.reactivations) == 4

    def test_mass_deactivation_is_refused(self):
        # A scrape returning one of three devices means the parser broke.
        plan = sheets.plan_changes(
            BASE_SHEET, [device("Apple", "iPhone 15 Pro", "128GB", 520)],
            CONDITIONS, today=TODAY, max_deactivation_ratio=0.30)
        assert plan.deactivations == []

    def test_prices_still_update_when_deactivation_is_refused(self):
        plan = sheets.plan_changes(
            BASE_SHEET, [device("Apple", "iPhone 15 Pro", "128GB", 599)],
            CONDITIONS, today=TODAY, max_deactivation_ratio=0.30)
        assert plan.deactivations == []
        assert len(plan.price_updates) == 4

    def test_no_deactivate_flag_is_honoured(self):
        plan = sheets.plan_changes(
            BASE_SHEET, [device("Apple", "iPhone 15 Pro", "128GB", 520)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert plan.deactivations == []


class TestGuards:
    def test_empty_conditions_tab_is_an_error(self):
        with pytest.raises(sheets.SheetError):
            sheets.plan_changes(BASE_SHEET, [device("A", "B", "128GB", 1)], [], today=TODAY)

    def test_blank_rows_are_ignored(self):
        rows = BASE_SHEET + [[""] * 11, []]
        plan = sheets.plan_changes(
            rows, [device("Apple", "iPhone 15 Pro", "128GB", 520)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert plan.unchanged == 4


class FakeWorksheet:
    """Records what would be written, so we can assert which columns are touched."""

    def __init__(self):
        self.updates = []
        self.appended = []

    def batch_update(self, updates, **kwargs):
        self.updates.extend(updates)

    def append_rows(self, rows, **kwargs):
        self.appended.extend(rows)


class TestOnlySafeColumnsAreWritten:
    """F, H, J and K may be written. The formula column I and the condition
    label G never may, so a sync cannot replace a formula with a literal."""

    def _apply(self, scraped, **kwargs):
        plan = sheets.plan_changes(BASE_SHEET, scraped, CONDITIONS, today=TODAY, **kwargs)
        writer = sheets.SheetWriter("fake-id", "fake.json")
        fake = FakeWorksheet()
        writer._worksheet = lambda _title: fake
        writer.apply(plan, CONDITIONS, len(BASE_SHEET), today=TODAY)
        return fake

    def test_updates_touch_only_columns_f_h_j_and_k(self):
        fake = self._apply([device("Apple", "iPhone 15 Pro", "128GB", 560)],
                           deactivate_missing=True, max_deactivation_ratio=1.0)
        columns = {u["range"][0] for u in fake.updates}
        assert columns <= {"F", "H", "J", "K"}, f"sync wrote outside F/H/J/K: {columns}"

    def test_the_formula_and_condition_columns_are_never_written(self):
        fake = self._apply([device("Apple", "iPhone 15 Pro", "128GB", 560)],
                           deactivate_missing=True, max_deactivation_ratio=1.0)
        columns = {u["range"][0] for u in fake.updates}
        assert "I" not in columns and "G" not in columns

    def test_new_device_appends_four_rows(self):
        fake = self._apply(
            [device("Apple", "iPhone 15 Pro", "128GB", 520),
             device("Samsung", "Galaxy S23", "128GB", 250),
             device("Google", "Pixel 8 Pro", "128GB", 300),
             device("OnePlus", "OnePlus 12", "256GB", 320)])
        assert len(fake.appended) == 4

    def test_nothing_is_ever_deleted(self):
        fake = self._apply([device("Apple", "iPhone 15 Pro", "128GB", 520)],
                           max_deactivation_ratio=1.0)
        # Deactivation writes FALSE into column J; no clear/delete call exists.
        assert not hasattr(fake, "clear")
        assert any(u["values"] == [["FALSE"]] for u in fake.updates)


def priced_device(manufacturer, model, storage, prices):
    """A device carrying a real price per condition, as KSP supplies."""
    best = prices["Like New"]
    return ScrapedDevice(
        manufacturer=manufacturer, model=model, storage=storage,
        price=best, currency="ILS", source="ksp", source_url="https://example.test/",
        condition_prices=dict(prices),
        condition_multipliers={c: round(p / best, 4) for c, p in prices.items()},
    )


# Real shekel prices, as KSP quotes them.
KSP_PRICES = {"Like New": 520, "Intact": 510, "Cracked": 275, "Faulty": 275}


class TestRealConditionPrices:
    """Each row stores the price for its own condition, in shekels, exactly as
    the source quotes it. The multiplier column is vestigial and always 1.0."""

    def test_each_row_stores_its_own_condition_price(self):
        rows = sheets.build_new_rows(
            "APL-X-128", priced_device("Apple", "iPhone 15 Pro", "128GB", KSP_PRICES),
            CONDITIONS, 2, today=TODAY)
        stored = {r[sheets.COL_CONDITION - 1]: r[sheets.COL_BASE_PRICE - 1] for r in rows}
        assert stored == KSP_PRICES

    def test_the_multiplier_is_always_one(self):
        rows = sheets.build_new_rows(
            "APL-X-128", priced_device("Apple", "iPhone 15 Pro", "128GB", KSP_PRICES),
            CONDITIONS, 2, today=TODAY)
        assert all(r[sheets.COL_MULTIPLIER - 1] == 1.0 for r in rows)

    def test_the_formula_reproduces_the_price_exactly(self):
        # F x 1.0 is the quoted price, with no rounding in between.
        rows = sheets.build_new_rows(
            "APL-X-128", priced_device("Apple", "iPhone 15 Pro", "128GB", KSP_PRICES),
            CONDITIONS, 2, today=TODAY)
        for row in rows:
            condition = row[sheets.COL_CONDITION - 1]
            computed = round(row[sheets.COL_BASE_PRICE - 1] * row[sheets.COL_MULTIPLIER - 1])
            assert computed == KSP_PRICES[condition]

    def test_existing_rows_are_repriced_per_condition(self):
        plan = sheets.plan_changes(
            BASE_SHEET, [priced_device("Apple", "iPhone 15 Pro", "128GB", KSP_PRICES)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert {u[3] for u in plan.price_updates} == set(KSP_PRICES.values()) - {520}

    # Resetting stale multipliers is covered by TestMigratingALegacySheet,
    # which starts from a sheet that actually has them.

    def test_release_year_from_the_source_is_written(self):
        device = priced_device("Apple", "iPhone 15 Pro", "128GB", KSP_PRICES)
        device.release_year = 2023
        rows = sheets.build_new_rows("APL-X-128", device, CONDITIONS, 2, today=TODAY)
        assert all(r[sheets.COL_RELEASE_YEAR - 1] == 2023 for r in rows)

    def test_second_run_is_a_no_op(self):
        device = priced_device("Apple", "iPhone 15 Pro", "128GB", KSP_PRICES)
        rows = [sheets.HEADER] + sheets.build_new_rows(
            "APL-IP15P-128", device, CONDITIONS, 2, today=TODAY)
        plan = sheets.plan_changes(rows, [device], CONDITIONS, today=TODAY)
        assert plan.is_empty, plan.summary()

    def test_a_source_without_condition_prices_falls_back_to_the_multipliers(self):
        # The PelePhone-style adapter gives one price per device. The Conditions
        # tab ratio is then applied here, so column F still means "what this
        # device in this condition is worth" and H is still 1.0.
        plain = device("Apple", "iPhone 15 Pro", "128GB", 520)
        rows = sheets.build_new_rows("APL-X-128", plain, CONDITIONS, 2, today=TODAY)
        assert [r[sheets.COL_BASE_PRICE - 1] for r in rows] == [520, 442, 312, 104]
        assert all(r[sheets.COL_MULTIPLIER - 1] == 1.0 for r in rows)

    def test_formula_column_is_still_never_written(self):
        plan = sheets.plan_changes(
            BASE_SHEET, [priced_device("Apple", "iPhone 15 Pro", "128GB", KSP_PRICES)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        writer = sheets.SheetWriter("fake-id", "fake.json")
        fake = FakeWorksheet()
        writer._worksheet = lambda _t: fake
        writer.apply(plan, CONDITIONS, len(BASE_SHEET), today=TODAY)
        columns = {u["range"][0] for u in fake.updates}
        assert columns <= {"F", "H", "J", "K"}
        assert "I" not in columns and "G" not in columns


class TestMigratingALegacySheet:
    """A sheet still in the euro-era layout -- one base price per device with
    the Conditions tab ratios in column H -- is brought onto the new one by an
    ordinary sync, with no special migration path in the code."""

    LEGACY = sheet_rows(
        ("APL-IP15P-128", "Apple", "iPhone 15 Pro", "128GB", 520, "TRUE"),
        legacy=True)

    def test_every_multiplier_is_reset_to_one(self):
        plan = sheets.plan_changes(
            self.LEGACY, [priced_device("Apple", "iPhone 15 Pro", "128GB", KSP_PRICES)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert {u[3] for u in plan.multiplier_updates} == {1.0}
        assert len(plan.multiplier_updates) == 3      # Like New was already 1.0

    def test_each_row_gets_its_own_price(self):
        plan = sheets.plan_changes(
            self.LEGACY, [priced_device("Apple", "iPhone 15 Pro", "128GB", KSP_PRICES)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert {u[3] for u in plan.price_updates} == set(KSP_PRICES.values()) - {520}

    def test_the_migration_is_idempotent(self):
        device_obj = priced_device("Apple", "iPhone 15 Pro", "128GB", KSP_PRICES)
        migrated = [sheets.HEADER] + sheets.build_new_rows(
            "APL-IP15P-128", device_obj, CONDITIONS, 2, today=TODAY)
        plan = sheets.plan_changes(migrated, [device_obj], CONDITIONS, today=TODAY)
        assert plan.is_empty, plan.summary()


class TestReadingCurrencyFormattedCells:
    """Google returns a currency-formatted cell with its symbol attached. If the
    symbol is not stripped the old value reads as None, every row looks changed,
    and the sync rewrites the whole sheet on every run."""

    def test_shekel_formatted_prices_are_read(self):
        rows = sheet_rows(("APL-IP15P-128", "Apple", "iPhone 15 Pro", "128GB", 520, "TRUE"))
        for row in rows[1:]:
            row[sheets.COL_BASE_PRICE - 1] = "\u20aa" + row[sheets.COL_BASE_PRICE - 1]

        plan = sheets.plan_changes(
            rows, [device("Apple", "iPhone 15 Pro", "128GB", 520)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert plan.price_updates == [], "shekel-formatted cells were not parsed"
        assert plan.unchanged == 4

    def test_euro_formatted_prices_are_still_read(self):
        # A sheet part-way through the migration can still hold euro formatting.
        rows = sheet_rows(("APL-IP15P-128", "Apple", "iPhone 15 Pro", "128GB", 520, "TRUE"))
        for row in rows[1:]:
            row[sheets.COL_BASE_PRICE - 1] = "\u20ac" + row[sheets.COL_BASE_PRICE - 1]
        plan = sheets.plan_changes(
            rows, [device("Apple", "iPhone 15 Pro", "128GB", 520)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert plan.price_updates == []

    def test_thousands_separators_are_read(self):
        rows = sheet_rows(("APL-X-1TB", "Apple", "iPhone 15 Pro Max", "1TB", 1725, "TRUE"))
        for row in rows[1:]:
            value = int(row[sheets.COL_BASE_PRICE - 1])
            row[sheets.COL_BASE_PRICE - 1] = f"\u20aa{value:,}"
        plan = sheets.plan_changes(
            rows, [device("Apple", "iPhone 15 Pro Max", "1TB", 1725)],
            CONDITIONS, today=TODAY, deactivate_missing=False)
        assert plan.price_updates == []
