# Accounts and uploads

Accounts are local to the installation. There is no hosted identity service and no account data leaves the server. The first browser visit creates the owner account; the owner creates member accounts from Settings.

Each member has a separate saved-model library, notes, collections, and download history. Passwords are changed from Settings; rotating a password signs out the account's other sessions.

## Saved models, notes, collections

The heart on a Hub model saves it without downloading. Saved models can be organized into collections — a project shortlist, a target rig. Notes are per-model threads: private to their author until shared with the other members of the installation.

## Uploads

The Uploads workspace creates repositories under the signed-in account's name:

```text
models/
  your-username/
    your-repository/
      .modelmanifest.json
      config.json
      model.safetensors
      ...
```

Model files stay in the model mount, not in the metadata database.

Choose a model folder in the browser and the files are sent in bounded chunks. Interrupted uploads keep their progress and resume from the server's confirmed offset. Uploaded repositories are private by default; their owner can share them with every local account.

Upload paths are confined to repositories owned by the signed-in account. Deleting a repository verifies ownership and requires the exact repository name.

## Disabling accounts

`ACCOUNTS_ENABLED=false` removes sign-in entirely and creates a single local compatibility identity. `ALLOW_ACCOUNTLESS=true` must also be set. Without accounts, everything is shared and unauthenticated, so this mode belongs on a trusted network.
