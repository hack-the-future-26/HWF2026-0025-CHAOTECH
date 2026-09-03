# 🚀 Hack the Future 26
## Team Repository Guide

Welcome to the official GitHub repository for **Team `HTF26-024-Chao-Tech`**.

This repository is your team's workspace for developing and submitting your Hack the Future 26 project.

You will use this repository to:

- Store your project code
- Work together with your teammates
- Keep track of changes
- Review each other's work
- Submit your final project

---

# 🔐 Important: How This Repository Works

The `main` branch is **protected**.

This means:

> **You cannot directly push your changes to `main`.**

Don't worry. This is intentional and helps prevent someone from accidentally breaking the team's main code.

Instead, everyone should work on their **own branch** and then create a **Pull Request**.

The basic workflow is:

```text
Create a branch
      ↓
Make your changes
      ↓
Save your changes
      ↓
Push your branch
      ↓
Create a Pull Request
      ↓
Another teammate reviews it
      ↓
1 approval required
      ↓
Merge into main
Think of main as the team's safe and stable version of the project.

🟢 1. Start Working on the Project

When you open the repository, you will see options such as:

Code
Issues
Pull requests
Actions

You normally do not need to change anything in the repository settings.

The organizers have already configured the repository rules.

🌿 2. Create Your Own Branch

A branch is your own working area inside the repository.

For example:

main
│
├── feature-login
├── feature-ai-model
├── fix-camera
└── docs-readme

You work on your branch instead of directly changing main.

🖱️ Method A: Using GitHub Website

This is the easiest method if you are new to Git.

Step 1

Open the repository on GitHub.

Click the branch selector near the top of the file list.

You will see something similar to:

main ▼
Step 2

Type the name of your new branch.

Example:

feature-login

Step 3

GitHub will show an option similar to:

Create branch: feature-login from main

Click it.

🎉 Your branch has now been created.

💻 Method B: Using Git

If you are using Git on your computer:

git checkout -b feature-login

Then check your current branch:

git branch

You should see:

* feature-login
  main

The * means you are currently working on feature-login.

🛠️ 3. Make Your Changes

Now work normally.

You can:

Add files
Edit files
Delete files
Add features
Fix bugs
Improve documentation

Your changes are happening on your branch, not directly on main.

💾 4. Save Your Changes

There are two ways to save your work to GitHub.

🖱️ Method A: Using GitHub Website

If you are creating or editing a file directly on GitHub:

Open the file.
Click the pencil/Edit button.
Make your changes.
Scroll down to the commit section.
Enter a short description.

Example:

Add login page
Choose:

Create a new branch for this commit and start a pull request

Click Propose changes.

Your changes will now be saved to a branch.

💻 Method B: Using Git

After changing files on your computer:

git add .

Create a commit:

git commit -m "Add login page"

A commit is basically a saved checkpoint of your work.

⬆️ 5. Push Your Branch to GitHub

If you are using Git locally:

git push origin feature-login

Your branch will now appear on GitHub.

If you are using the GitHub website, you do not need this step.

🔀 6. Create a Pull Request

A Pull Request, usually called a PR, means:

"I finished my changes. Can someone check them before they become part of main?"

🖱️ Creating a PR from GitHub

After pushing your branch, GitHub may show:

Compare & pull request

Click it.

If you don't see it:

Open Pull requests.
Click New pull request.
Select:
base: main
compare: your-branch

Example:

base: main
compare: feature-login
Add a clear title

Good:

Add user login system

Bad:

changes
Explain what you did

Example:

## What I changed

- Added login page
- Added email validation
- Added logout button

## Testing

- Tested login with valid credentials
- Tested invalid password

Then click:

Create pull request

👀 7. Ask a Teammate to Review

Your Pull Request needs to be checked.

Our repository requires:

At least 1 approval before merging.

A teammate should check:

Does the code work?
Does the feature do what it should?
Is anything broken?
Is the code understandable?
Are there unnecessary changes?
Are passwords or API keys accidentally included?

If everything looks good, the reviewer can click:

Approve

💬 8. What If the Reviewer Finds a Problem?

Don't worry.

You do not need to create another Pull Request.

Make the required changes on the same branch.

For example:

Reviewer:
"Please fix the login validation."

        ↓

You fix it

        ↓

Commit the change

        ↓

Push the branch

        ↓

The existing PR automatically updates

The reviewer can then check the new changes.

✅ 9. Merge the Pull Request

Once the Pull Request has received the required approval:

Check that the required approval is present.
Check that there are no important problems.
Click Merge pull request.
Confirm the merge.

The repository allows:

Merge commit
Squash and merge
Rebase and merge

If your team isn't sure which one to use, Squash and merge is a simple choice for many small hackathon changes.

🚫 10. Don't Push Directly to Main

Do not try to push directly to main.

For example, this is not allowed:

git push origin main

Instead:

Create branch
      ↓
Make changes
      ↓
Pull Request
      ↓
1 teammate approves
      ↓
Merge

This protects everyone's work.

🔥 11. Never Force-Push to Main

Do not try to force-push to main.

Avoid:

git push --force

The protected main branch is designed to prevent this.

🔑 12. NEVER Upload Passwords or API Keys

Very important.

Never put these inside your repository:

API keys
Passwords
Access tokens
Private keys
Database passwords
.env files containing real secrets

For example, do NOT commit:

API_KEY=123456789abcdef

inside a public repository.

If you accidentally upload a secret:

Tell the organizers immediately.

Simply deleting the file may not be enough because the secret could still exist in Git history.

📁 13. Keep the Repository Organized

Try to keep the project clean.

For example:

project/
│
├── src/
├── docs/
├── tests/
├── README.md
├── requirements.txt
└── .gitignore

Don't upload unnecessary files such as:

Huge videos
Temporary files
Build files
Personal files
Passwords
IDE-specific junk

Use .gitignore where appropriate.

🏷️ 14. Use Clear Branch Names

Good examples:

feature-login
feature-chatbot
feature-dashboard
fix-payment-error
fix-camera-bug
docs-installation

Avoid names such as:

test
abc
new
branch1
mybranch
asdf

A clear branch name makes teamwork easier.

📝 15. Use Clear Commit Messages

A commit message should tell your teammates what you changed.

Good
Add login page
Fix camera initialization
Add chatbot API
Update project documentation
Avoid
changes
update
done
final
final2
final-final

Keep commit messages short and meaningful.

🧑‍💻 16. Recommended Team Workflow

For every new feature:

1. Start from main
        ↓
2. Create a new branch
        ↓
3. Work on the feature
        ↓
4. Commit your changes
        ↓
5. Push the branch
        ↓
6. Create Pull Request
        ↓
7. Teammate reviews
        ↓
8. Get 1 approval
        ↓
9. Merge into main
        ↓
10. Start the next feature
🆘 17. If You Get Stuck
GitHub says your push was rejected

Check that you are not trying to push directly to main.

Create a branch instead.

Your Pull Request cannot be merged

Check whether:

You have the required approval.
There are merge conflicts.
GitHub is showing another problem.
You accidentally committed a secret

Tell the organizers immediately.

You don't understand Git

That's okay.

Ask your teammates or the Hack the Future 26 organizers for help.

🏆 Hack the Future 26

Build together. Review together. Ship together.

Keep main stable.

Work in branches.

Use Pull Requests.

Get your teammate's approval.

Then merge.

Happy hacking! 🚀