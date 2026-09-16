# Test photos

Downscaled (max 960 px) copies used by `backend/test_photo_checks.py` to check
that the pothole model finds a pothole in a real photo and nothing on a clean
road. Both are from Wikimedia Commons and keep their original licences.

| File | Source | Author | Licence |
|---|---|---|---|
| `pothole.jpg` | [File:Vddj-2.jpg](https://commons.wikimedia.org/wiki/File:Vddj-2.jpg) | Velazquez Dominguez Diana | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
| `clean_road.jpg` | [File:A national highway.JPG](https://commons.wikimedia.org/wiki/File:A_national_highway.JPG) | Thamizhpparithi Maari | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) |

Changes: resized. Neither photo was part of the model's training data as far
as we know; both were in the held-out evaluation set (`models/README.md`).
