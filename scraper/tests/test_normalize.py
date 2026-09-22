import pytest

import normalize


class TestStorage:
    @pytest.mark.parametrize("text,expected", [
        ("128GB", "128GB"),
        ("128 GB", "128GB"),
        ("256gb", "256GB"),
        ("1TB", "1TB"),
        ("1 TB", "1TB"),
        ("1024GB", "1TB"),          # normalised to the larger unit
        ("2048 GB", "2TB"),
        ("512ג״ב", "512GB"),        # Hebrew gigabyte
        ("iPhone 15 Pro 128GB", "128GB"),
        ("1,024 GB", "1TB"),
    ])
    def test_parses(self, text, expected):
        assert normalize.storage(text) == expected

    @pytest.mark.parametrize("text", ["", "iPhone 15 Pro", "no digits here"])
    def test_rejects_unreadable(self, text):
        with pytest.raises(normalize.NormalizeError):
            normalize.storage(text)


class TestManufacturer:
    @pytest.mark.parametrize("text,expected", [
        ("Apple", "Apple"),
        ("apple", "Apple"),
        ("אפל", "Apple"),
        ("אייפון", "Apple"),
        ("Samsung Galaxy S23", "Samsung"),
        ("סמסונג", "Samsung"),
        ("גוגל", "Google"),
        ("Pixel 8 Pro", "Google"),
        ("OnePlus", "OnePlus"),
        ("one plus 12", "OnePlus"),
        ("שיאומי", "Xiaomi"),
    ])
    def test_maps(self, text, expected):
        assert normalize.manufacturer(text) == expected

    def test_rejects_unknown_brand(self):
        with pytest.raises(normalize.NormalizeError):
            normalize.manufacturer("Frobozz Magic Phone Co")

    def test_longest_alias_wins(self):
        # "one plus" must beat a bare "one" appearing inside the string.
        assert normalize.manufacturer("one plus 12") == "OnePlus"


class TestModel:
    @pytest.mark.parametrize("text,brand,expected", [
        ("Apple iPhone 15 Pro 256GB 5G", "Apple", "iPhone 15 Pro"),
        ("iPhone 15 Pro", "Apple", "iPhone 15 Pro"),
        ("Galaxy S23 128GB dual sim", "Samsung", "Galaxy S23"),
        ("Pixel 8 Pro  -  ", "Google", "Pixel 8 Pro"),
    ])
    def test_cleans(self, text, brand, expected):
        assert normalize.model(text, brand) == expected

    def test_rejects_empty_after_cleaning(self):
        with pytest.raises(normalize.NormalizeError):
            normalize.model("128GB", None)


class TestPrice:
    @pytest.mark.parametrize("text,expected", [
        ("1250", 1250.0), ("₪1,250", 1250.0), ("  2,399 ₪ ", 2399.0),
        (1810, 1810.0), (1810.5, 1810.5), ("1250.50", 1250.5),
    ])
    def test_parses(self, text, expected):
        assert normalize.price(text) == expected

    def test_rejects_unreadable(self):
        with pytest.raises(normalize.NormalizeError):
            normalize.price("call us")


class TestMatchKey:
    def test_ignores_case_space_and_punctuation(self):
        assert (normalize.match_key("Apple", "iPhone 15 Pro", "128GB")
                == normalize.match_key("apple", "iphone-15  pro", "128 gb"))

    def test_distinguishes_storage(self):
        assert (normalize.match_key("Apple", "iPhone 15 Pro", "128GB")
                != normalize.match_key("Apple", "iPhone 15 Pro", "256GB"))

    def test_distinguishes_model(self):
        assert (normalize.match_key("Apple", "iPhone 15 Pro", "128GB")
                != normalize.match_key("Apple", "iPhone 15", "128GB"))


class TestDeviceId:
    def test_follows_phase1_convention(self):
        # Phase 1 IDs look like APL-IP15P-128 -- prefix, slug, size without "GB".
        assert normalize.make_device_id("Apple", "iPhone 16 Pro", "256GB") == "APL-IPHONE16PRO-256"

    def test_keeps_tb_suffix(self):
        assert normalize.make_device_id("OnePlus", "OnePlus 12", "1TB").endswith("-1TB")

    def test_avoids_collisions(self):
        taken = {"APL-IPHONE16PRO-256"}
        assert normalize.make_device_id("Apple", "iPhone 16 Pro", "256GB", taken=taken) \
            == "APL-IPHONE16PRO-256-2"

    def test_unknown_brand_still_gets_a_prefix(self):
        assert normalize.make_device_id("Fairphone", "Fairphone 5", "256GB").startswith("FAI-")
