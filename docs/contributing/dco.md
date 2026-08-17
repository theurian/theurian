# Developer Certificate of Origin

Theurian requires a DCO sign-off on every commit. There is no CLA; see
[ADR-0015](../adr/0015-dco-over-cla.md) for why.

> **Sign-off is not the same as signing.** `main` also requires each commit to
> carry a verified cryptographic *signature*, which `git commit -s` does not add.
> Set that up once — see
> [Signing your commits](https://github.com/theurian/theurian/blob/main/CONTRIBUTING.md#signing-your-commits).

Sign off with `git commit -s`, which appends:

```text
Signed-off-by: Your Name <you@example.com>
```

If you forget, `git commit --amend -s` fixes the most recent commit and
`git rebase --signoff main` fixes a branch.

The full text follows, verbatim, from <https://developercertificate.org/>.

---

```text
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```
