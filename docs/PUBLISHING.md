# Publish this project on GitHub

These instructions publish **source code**, not a running service. The application needs a server; GitHub Pages cannot run this backend.

## 1. Prepare your local folder

Extract the clean project ZIP and open a terminal **inside this project's folder**, where `README.md / README.it.md`, `.gitignore` and `docker-compose.yml` are located. Publish the extracted files, not the ZIP itself.

Install Git and sign in to GitHub. If using HTTPS, Git Credential Manager can open your browser for authentication. Never put an access token in a remote URL.

The release contains `.env.example` with blank secrets. Copy it to `.env` only on your development/deployment machine. `.env`, databases, uploaded files, logs and backups are excluded by `.gitignore`. Existing runtime data has not been included. Keep the supplied `.gitignore` and `.dockerignore`.

## 2. Create an empty repository

On GitHub, choose **New repository**, select its owner, choose a name and visibility, and click **Create repository**. Do not initialize it with a README, license or `.gitignore`: this folder already has its own files.

Suggested name: `sentinel-td`. A public repository makes the source and included assets visible to everyone. No project license was present in the archives; select a license appropriate to the rights you hold before granting reuse rights. Existing third-party licenses still apply.

## 3. Commit and push

The following Git commands work in PowerShell, Bash and most terminals. Replace `YOUR_ACCOUNT` and `YOUR_REPOSITORY` with your actual values.

```sh
git init
git branch -M main
git status --short
git add .
git diff --cached --stat
git diff --cached --name-only
git commit -m "Sentinel TD 2.4.0"
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

Before committing, check the staged file list. It should contain source files, documentation and `.env.example`, not `.env`, database files, uploads or backups. Do not use `git add -f` to bypass the ignore rules.

If Git asks for your identity, set your own name and GitHub email (or GitHub's private noreply address):

```sh
git config user.name "Your Name"
git config user.email "YOUR_GITHUB_EMAIL"
```

Then repeat the commit and push. If a remote already exists, inspect it with `git remote -v`; use `git remote set-url origin ...` only to deliberately change it. A rejected push to a nonempty repository should be reconciled with its history; do not force-push over other work.

## 4. Review the repository

Open the repository and verify that the README is displayed, the file tree is correct and the Actions checks finish. Set the description and topics in **About**. Review the language catalogs and installation instructions. The included container publishing workflow, where present, is manual; pushing source does not deploy a service.

For a versioned release after verification:

```sh
git tag v2.4.0
git push origin v2.4.0
```

Use another unused version tag if this one already exists. You can then create a GitHub Release from the tag. Do not attach archives containing your local `.env` or runtime data.

## 5. Publish later changes

```sh
git status
git add .
git diff --cached
git commit -m "Describe the change"
git push
```

Deploy separately on your server using the main README. Never use this clean release to overwrite a production database.

## 6. Connector packages

Connector sources live in `connectors/`. Build the installable zips locally; the registration key is read from the environment and written only into the generated WordPress package:

```sh
SENTINEL_HUB_KEY=your-registration-key python scripts/build_connectors.py
```

The packages are created in `dist/`, which is excluded by `.gitignore`. Never commit them: the WordPress package contains the key.

## References

- [GitHub: adding locally hosted code](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)
- [GitHub: managing remotes](https://docs.github.com/en/get-started/git-basics/managing-remote-repositories)
- [Docker Compose environment interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)
