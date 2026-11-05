# Contributing

Use the seven AMD guide directories and lower-case custom names. README.md,
LICENSE and SKILL.md retain their platform-standard spellings. Keep original
experimental records and distinguish software, simulation and physical results.

After a source/document change run python build/tools/check_repository.py,
the tests in build/tests, the source contract audit, and the affected functional
checks. No SHA256 inventory update is required. For model/RTL changes run smoke
then full validation and obtain new board evidence where applicable.

Generated work, external inputs and newly built packages remain ignored. Keep
the root README directory correspondence and commands synchronized with moves.
