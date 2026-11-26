# OmniWM v0.8.0 Intel 互換版: 調査結果と実装計画

## Context

[intel-compatibility.md](intel-compatibility.md)(仕様)に基づき、OmniWM v0.8.0 の x86_64 / macOS 26 互換版を作る。
本書は読み取り専用調査の結果と実装計画で、2026-11-27 に承認された。§9 は推奨案を採用する(D8 の push は実行時に改めて許可を得る)。

表記: 【確認】コード・コマンド出力・一次資料で確認 / 【推定】静的調査からの推定 / 【実験】実行しないと分からない

調査時点では、ビルド、テスト、依存の導入、VCS の書き込み、push、Actions の実行をしていない。

---

## 1. 現状と結論

### リポジトリと開発機【確認】
- 調査開始時の `@` は `docs/` の追加のみで、親が `v0.8.0` = `bf118cca`。origin = `shuheykoyama/OmniWM`(public fork)、upstream = `OmniNull/OmniWM`。`main@upstream` は `f93742d5`(2026-11-26)で、origin の `main` はその 1 つ前。fork の Actions 権限は有効だが、登録済み workflow と実行履歴は 0 件(最初の push 時に UI での有効化が必要かもしれない)。
- 実機は Intel MacBook Pro(`MacBookPro16,4`、UHD 630 + Radeon Pro 5600M、外部ディスプレイあり)。`sw_vers` は macOS 26.7.3(25G342)。SIP 有効、Xcode 26.6 / SDK 26.5。

### 結論
1. **CI クロスコンパイル(`xcode-27` arm64 runner)で x86_64 版を作れる見込み**。自社コードに `#if arch`、NEON、inline asm、`Float16`、直接の `objc_msgSend` は見つからなかった【確認】。Luau はインタプリタのみで、SSE 経路は CPUID 分岐付き。
2. 主なビルド阻害は **GhosttyKit が arm64 専用**であることと、`macos-arm64` / `--arch arm64` の固定。上流 Ghostty には universal の生成経路が残っている。
3. **ローカル Intel でのそのままのビルドは不可**。Xcode 27 は Apple silicon 専用で、コードは 27 SDK の API を使う(後述)。
4. 実行時の最大の不確定要素は **OmniShell の Metal テクスチャ(`.shared` 固定)**。修正が必要になる可能性が高い。
5. 長期の最大リスクは、**上流が macOS 26 を打ち切る意向**を表明していること。打ち切り後は上流の新版を取り込めない。

### 履歴【確認】
- v0.5.2.1 は universal(`--arch arm64 --arch x86_64`、`macos-arm64_x86_64/libghostty.a`、macOS 15)。
- `bc0ac013` と `35a9a0e0`(2026-07-02)で arm64 化と macOS 26 化。変更はビルド設定と OS 下限の整理だけで、アーキテクチャ固有のソース変更はない。
- `07bc8b08` で一度 27 に引き上げ、`10c1d92b`(0.5.3.1)で 26 に戻した。`59e03535`(0.5.3.2)で FoundationModels を weak link 化。
- 以降の関連変更: `26f0fd1d`(リリース時に最新の Ghostty をビルドする経路、現在は arm64 専用)、`565b5241`(OmniShell 統合)、`fa88a95d` と `b389ed5e`(Swift 6.5 スナップショット固定)。
- 上流の PR [#1086](https://github.com/OmniNull/OmniWM/pull/1086)(Intel canary)は未マージで閉じられた。メンテナーは Intel を予定しておらず、macOS 26 も近く打ち切ると回答([#548](https://github.com/OmniNull/OmniWM/issues/548) も同趣旨)。
  - 流用する案: universal を作って thin 化、`OMNIWM_ARCH` による切り替え。
  - 流用しない案: ディレクトリの有無で分岐する `Package.swift`、Intel 用 ZIP を受け入れる Updater。
  - 同 PR は本体ビルドも起動も未検証と明記している。

---

## 2. ビルド阻害要因

| 区分 | 阻害要因 | 根拠 | 状態 |
|---|---|---|---|
| コンパイル | `GenerationOptions(samplingMode:)` は SDK 26.5 にない(`sampling` のみ) | `FoundationModelsIssueEngine.swift:40`、ローカル SDK の swiftinterface | 【確認】27 SDK が必須(失敗すること自体は【推定】) |
| コンパイル | Xcode 27 は Apple silicon 専用(macOS 26.6 以上)。deployment target 27 未満なら x86_64 は標準 arch に残る | [Xcode 27 release notes](https://developer.apple.com/documentation/xcode-release-notes/xcode-27-release-notes) | 【確認】26.0 で残るのは規則からの読み |
| コンパイル | Swift `main-snapshot-2026-11-01` と 27 SDK の x86_64 向けコンパイル。全ターゲットが warnings-as-errors | `.swift-version`、`Package.swift` | 【実験】26.5 SDK には FoundationModels / SwiftUI / SkyLight の x86_64 がある |
| リンク | `-L` が `macos-arm64` 固定 | `Package.swift:6,160` | 【確認】 |
| リンク | GhosttyKit の公式配布物は arm64 専用 | `Scripts/dev-tools.env`、`build-ghosttykit.py:89,108-123` | 【確認】 |
| リンク | ビルダーは arm64 ホストと `native` slice を要求し、revision は上流 `main` の最新 | `build-ghosttykit.py:56,89,147` | 【確認】固定 revision のビルドには使えない |
| パッケージ | `--arch arm64` 固定 | `package-app.sh:22`、`Makefile:57`、`localization.py:643` | 【確認】 |
| パッケージ | preflight が arm64 のみ許可 | `ghostty-preflight.sh:34-36,72-82` | 【確認】 |
| パッケージ | 署名の既定は上流の Developer ID と公証 | `package-app.sh:25` | 【確認】fork では使えない |
| 対象外にする | `arc-audit.sh`、`omniwm_release.py`、`release.yml`、`doctor.sh` は arm64 前提 | 各ファイル | Intel の経路では使わず、変更しない |
| 実行時 | Metal テクスチャが全 5 箇所で `.shared` | `ShellShaderTexture.swift:26`、`ShellShaderDrawer.swift:430`、`ShellGroupEffect.swift:327`、`ShellLayerRaster.swift:35`、`ShellSettingsPreviewRaster.swift:51` | 【実験】Apple は[非 Apple GPU の texture で shared は使えない](https://developer.apple.com/documentation/metal/mtlstoragemode/shared)と記載。生成失敗か assert abort の恐れ |
| 実行時 | SMC 温度は Apple silicon のキー(`Tp`/`Te`/`Tg`、型 `flt `)のみ | `ThermalMonitor.swift:47-52,69,76` | 【確認】Intel で空欄や誤分類になるのは【推定】 |
| 実行時 | Hidden Bar に OS 版チェックがない | `NativeMenuBarPreferences.swift:10` | 【確認】26 で有効化され動作するかは【未確認】 |
| 実行時 | シェーダーは 8 ms 超が 3 フレーム続くと恒久的に失敗扱い | `ShellShaderDrawer.swift:10-11,269-271` | 【実験】 |

### GhosttyKit(固定 `8265e74c`、Zig 0.17.0)
- 【確認】`-Dxcframework-target=universal`(上流の既定)が aarch64 と x86_64 を別々に作り `lipo` する([src/build](https://github.com/ghostty-org/ghostty/tree/8265e74cd3945c56d59aba3917ff01af33ba3e78/src/build))。`native` はホスト arch なので、arm64 ホストから x86_64 単体は作れない。
- 【確認】universal の出力名は `ghostty-internal.a`(`lib` なし)。thin 化して `libghostty-internal.a` に改名し、`xcodebuild -create-xcframework` で組み直す。
- 【確認】`bundle_compiler_rt = true` のため、#286 型の `___zig_probe_stack` 欠落は起きない見込み。最低 OS は 13.0。module map は `ghostty.h` の umbrella のみ。
- 【確認】Ghostty は低電力 GPU を選び、`hasUnifiedMemory` なら shared、そうでなければ managed を使う。UHD 630 で shared になる点は Apple の記載と緊張するので実機で確認する。
- 【推定】Zig の x86_64-macos は Tier 2([release notes](https://ziglang.org/download/0.17.0/release-notes.html)、検索要約経由)。クロスビルドなのでホストは arm64 のまま。
- 【実験】universal ビルドの成功、所要時間とディスク量。

その他の依存は swift-toml 2.0.0(`827506c9`)と同梱 Luau だけで、固定版を変えない。

---

## 3. 互換性マトリクス

| 機能 | 判定 | 根拠と確認事項 |
|---|---|---|
| Niri / Dwindle / ワークスペース / マルチディスプレイ / ルール / ショートカット | 維持可能【推定】 | arch 固有コードなし。使用する private symbol は SDK stub に x86_64 がある。フォーカス用 0x100 バイトの event record(`PrivateAPIs.swift:48-120`)は実機で確認 |
| トラックパッドジェスチャー | 未確認 | MultitouchSupport の構造体 stride 96(`MultitouchGestureFrame.swift:11-17`) |
| ウィンドウ枠、Overview、角丸設定 | 維持可能【推定】 | 角丸は 26.4 以上が必要で満たす |
| Quake Terminal(Ghostty) | 条件付き | x86_64 の GhosttyKit が必要。Swift 側に arm 前提はない |
| Quake(Kitty / Foot) | 維持可能【推定】 | 外部プロセス |
| OmniShell(Luau、ウィジェット、スタイル) | 維持可能【推定】 | インタプリタのみ。ScriptHost はアプリ内ライブラリ |
| Metal Effects | **未確認(要修正の見込み)** | 実行時コンパイル(`ShellShader.swift:304`)。tile shader などの Apple GPU 専用機能は検索で未検出。効果ごとに結果を記録 |
| ネイティブ glass | 未確認 | capability probe とフォールバックあり。公式 docs の確認済みは 27.2 のみ |
| 温度ウィジェット | 条件付き | Intel の SMC キー対応が必要 |
| GPU 使用率 | 未確認 | 最初の IOAccelerator を採用するため 2 GPU で要確認 |
| Now Playing、音量、visualizer | 条件付き | NowPlaying の x86_64 dylib を `/usr/bin/perl` が読み込む |
| ノッチ用 island | 維持可能 | ノッチがないため表示されない(上流と同じ挙動) |
| 外部の arm64 専用 provider | 対応困難 | 利用者側のバイナリ。本体では扱わない |
| Apple Intelligence | 維持可能(非 AI) | `@available(macOS 27)` と weak link(`Package.swift:147,162`)。手動送信は独立 |
| Hidden Bar | 対応困難(27 要件。仕様どおり非対応) | docs は 27 要件、コードは未ゲート。判断事項 D2 |
| 設定、CLI、IPC、ログイン項目、翻訳 | 維持可能【推定】 | arch 非依存。IPC は同一 UID と secret で認証 |
| Launcher、Command Palette、Clipboard | 維持可能【推定】 | Homebrew の `/usr/local` も考慮済み |
| Style gallery | 維持可能 | Luau と Metal ソースを取得するだけで本体は置き換えない |
| 更新検知・通知 / 自動インストール | 維持 / 遮断 | 下記 |

---

## 4. 修正対象と最小変更案

方針: 新規ファイルに寄せ、上流ファイルは条件分岐の数行にする。arm64 の既定動作は変えない。

### ビルド(必須)
| ファイル | 変更 | 理由と上流への影響 |
|---|---|---|
| `Package.swift` | `OMNIWM_ARCH`(既定 `arm64`)で Ghostty の `-L` を選ぶ。既存の `OMNIWM_LIVE_CHECKS` と同じ方式 | 約 2 行 |
| `Scripts/ghostty-preflight.sh` | `macos-x86_64` を許可し、期待 arch と digest(CI の provenance 由来の env)を切り替える | arm64 の経路は不変 |
| `Scripts/package-app.sh` | `--arch "${OMNIWM_ARCH:-arm64}"` と表示文言 | 約 3 行 |
| `Makefile`、`Scripts/localization.py` | 同じ変数を使う | 各 1 行。`test_localization.py` の既定値は不変 |
| 新規 `Scripts/intel/build_ghostty.py` | `build-ghosttykit.py` の検証関数を import。固定 revision、universal、thin、xcframework、provenance。Zig の SHA も固定 | 上流ファイルを編集しないので衝突しない |
| 新規 `Scripts/intel/verify_app.py` | 公式 arm64 版と構成、`Info.plist`、動的リンクを比較する。全 Mach-O が `x86_64` のみで最低 OS 26.0 以下。依存は OS 標準かつ公式版の範囲内、公式版が weak のものは weak、run path も公式版の範囲内。ad-hoc 署名と entitlements、zip の往復、provenance | 当初案の shell ではなく Python にして Gate 1 の検査を再利用する。`package-app.sh` には入れない |
| 新規 `Scripts/intel/install.sh` | 取得物の hash 確認、旧版と設定の退避、ローカル署名、配置、rollback | 秘密情報を CI に置かない |
| 新規 `.github/workflows/intel.yml` | 後述 | 既存 workflow は変更しない |

### Updater(必須)
経路は「起動時(1 日 1 回)またはメニュー / 設定からの手動確認 → `UpdateCoordinator.swift:317` が `installer.canInstall` を参照 → download、検証、置換(`AppSelfUpdater.swift:116`)→ 再起動(`:131`)」。IPC、CLI、URL scheme、スクリプトからの起点と、起動時に再開する保留更新は見つからなかった。資産の arch 検査はない。署名要件で偶然失敗する現状には依存しない。

| ファイル | 変更 |
|---|---|
| `AppSelfUpdater.swift` | `supportsSelfUpdate`(init 引数、既定は `#if arch(x86_64)` で false)を追加し、`canInstall` に AND、`install` と `relaunch` の先頭で `guard`。`AppUpdateInstalling` にも公開し、`UpdateCoordinator` が popup の `brew` 案内を省く |
| `UpdateWindowController.swift:124,146-220`、`GeneralSettingsTab.swift:64`、`Localizable.xcstrings` | Intel では `brew upgrade` の表示とコピーを隠し、手動の再ビルド更新を案内する。`brew` は公式版で上書きする手動経路になるため。文言は実行時分岐にして両 arch の翻訳検査を通す |
| `AppSelfUpdaterTests.swift`、`UpdateCoordinatorTests.swift` | 既存テストは true を注入する。false で `install` と `relaunch` が拒否すること、arch ごとの既定値、自動と手動の確認で通知は出るが Install と `brew` が出ないことを追加 |

検知、通知、「Open Release Page」、スキップは現状のまま。`CFBundleShortVersionString` は上流と同じ数値のみにする(接尾辞を付けると比較と style の ref が壊れる)。

### 実行時(実験結果と承認が条件)
| 対象 | 条件 | 最小変更案 |
|---|---|---|
| Metal の 5 箇所 | 段階 0 で必要と確認 | storage mode は CPU アーキテクチャではなく GPU の capability(`supportsFamily(.apple1)`)で選び、非 Apple family は `.managed`。managed を CPU で読む前は blit `synchronize` を発行し、コマンドバッファの完了を保証する。Apple silicon の描画経路は変えない。実際の OmniShell シェーダーで検証し、修正できない効果だけ個別に無効化 |
| `ThermalMonitor.swift` | E0 のキー一覧 | x86_64 で CPU / GPU キーと `sp78` を追加 |
| `NativeMenuBarPreferences.swift:10` | D2 の承認 | macOS 27 未満は `unavailable` を投げる。既存の UI / IPC のゲートがそのまま働き、設定値とシステム設定は書き換えない |

FoundationModels、Window Management、Quake の Swift コードは変更しない。

---

## 5. CI、toolchain、パッケージ

### 固定するもの
| 項目 | 値 |
|---|---|
| ビルド runner | `xcode-27`(arm64、3 CPU / 7 GB / 14 GB。[public repo は無料](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)) |
| Xcode | `DEVELOPER_DIR=/Applications/Xcode_27.0.app/...`(上流と同じ。[image](https://github.com/actions/runner-images/releases/tag/xcode-27-arm64/20261104.0337) の既定は 27.1) |
| Swift | `.swift-version` のまま、既存の `setup-swift` |
| Ghostty / Zig | `dev-tools.env` の `8265e74c` / `0.17.0` |
| target | `--arch x86_64`、macOS 26.0 |
| 実行確認 | `macos-26-intel`(4 CPU / 14 GB、無料)。[Xcode は 26.6 まで](https://raw.githubusercontent.com/actions/runner-images/main/images/macos/macos-26-Readme.md)なのでビルドには使わない |

### `intel.yml`(`intel/**` への push、`contents: read`、利用者が登録する secret は使わない、action は上流の SHA 固定)
手動実行(`workflow_dispatch`)は、この workflow が default branch にある場合だけ使える。そのためだけに `main` は変えず、`intel/v0.8.0` への push で実行する。上流の workflow は、この push では起動しない。

1. **ghostty**: runner の版と空き容量を記録し、`setup-lix`(gettext)、Metal Toolchain、`build_ghostty.py` を実行する。cache key は OS、arch、Xcode build、Metal compiler の版、`dev-tools.env`(revision と Zig 版)、2 つのビルドスクリプト(Zig の SHA とビルド設定)、flake。cache hit の場合も `verify_ghostty.py` を毎回実行する。artifact は xcframework、provenance、検証結果、runner の記録。`github.token`(読み取りのみ)は gettext 取得時の rate limit 回避にだけ使う。
2. **app**: `setup-swift` の後、ghostty job の artifact を取得して `verify_ghostty.py` で再検証し(cache hit でも省略しない)、`Frameworks/` に展開して provenance の digest を preflight に渡す。`OMNIWM_ARCH=x86_64` で `package-app.sh release dev`(証明書がないので ad-hoc。localization 検査込み)を実行し、`verify_app.py` が検査と zip 化を行う。artifact は zip、checksum、provenance、検証結果、runner の記録。
3. **test**: x86_64 の test bundle を作り、同じ runner の Rosetta で実行する(release notes は macOS 27 の Rosetta で Intel 開発が可能と記載)【実験】。
4. **intel-smoke**: `macos-26-intel` で `omniwmctl --help` と数秒の起動(dyld の欠落を検出)。ネイティブの `swift test --skip-build` は、Xcode 27 で作った bundle が 26.6 の XCTest で動くか不明なので試行扱い【実験】。
5. **arm64 の非回帰**: 既存の `ci.yml` を対象 branch に手動実行する。

費用は無料枠内の見込み。14 GB のディスクが不足する場合は、job の分割と cache の削減で対処する。

### 署名、Bundle ID、設定、rollback
- Bundle ID は `com.barut.OmniWM` のまま。設定パス、固定の IPC socket、起動競合チェックと整合する。公式 arm64 版は Intel で動かないので併存しない。
- ローカルで固定の証明書により再署名する(`--options runtime` と `OmniWM.entitlements`、内側から順)。ad-hoc のままだと再ビルドごとに TCC の許可が失われ得る([Apple forums](https://developer.apple.com/forums/thread/721745))。hardened runtime と perl の dylib 読み込みは実機で確認する。
- 権限は Accessibility と Input Monitoring が必須、Screen Recording は任意。`Displays have separate Spaces` はこの機体では有効。SIP は変更しない。
- artifact は `gh run download` で取得する(通常 quarantine は付かない【推定】)。
- 更新前に旧 `.app` の zip と `~/.config/omniwm`、`~/.local/state/omniwm` を退避し、rollback は両方を戻す。上流 `main` は設定を TOML から Luau に移しており、次版では設定の復元が必須になる見込み。

### 上流の取り込み
`v0.8.0` から `intel/v0.8.0` bookmark を作る。新版が出たら upstream を fetch し、Intel の変更を新 tag に rebase して、`.swift-version`、Ghostty pin、`LSMinimumSystemVersion` の差分を確認してから CI と実機確認を行う。**下限が 27 になった版は取り込まない。**

---

## 6. 代替案

| 案 | 評価 |
|---|---|
| A. `xcode-27` でクロスコンパイル(推奨) | 上流と同じ toolchain と SDK。修正は小。実行テストが分離する |
| B. universal の `.app` | 時間とディスクが増えるだけで、利点がない |
| C. Intel ローカルで SDK 26.5 + 6.5 snapshot | `samplingMode` の分岐などソース修正が必要。リンク SDK が公式版と変わり UI 差の恐れ。A が成立しない場合の予備 |
| D. macOS 26 の Intel runner でビルド | C と同じ制約で、再現性だけ改善する |

A の成立条件が崩れる点は、27 SDK の x86_64 interface 欠落と snapshot の x86_64 コンパイラ不具合。前者は C、後者は snapshot 日付の変更(理由を明示して承認を得る)で対処する。

---

## 7. 段階的な実装計画と検証ゲート

各ゲートを通るまで次に進まない。

| 段階 | 作業 | ゲート |
|---|---|---|
| 0 実機の事前実験 | scratchpad の小さなスクリプトで (a) 両 GPU の shared / managed と IOSurface texture 生成 (b) SMC キー一覧 (c) Hidden Bar の preference の有無(TCC の確認が出る場合がある) | 結果を記録し、実行時修正の要否を確定 |
| 1 依存 | 本書の保存、bookmark 作成、`build_ghostty.py`、`verify_ghostty.py`、ghostty job | Gate 1: ビルド成功。全 member が `x86_64` のみ。header が固定ソースと公式 zip に一致。xcframework の metadata が正しい。全 member の最低 OS が 26.0 以下。x86_64 の最小リンクが成功。provenance と検証結果が artifact にある |
| 2 本体 | arch 切り替え、Updater 変更、app job | Gate 2: 固定 toolchain の x86_64 release build と link が成功。`.app`、CLI、dylib が生成され `verify_app.py` に合格。Updater の制約をテスト。arm64 は `ci.yml` を対象 branch に手動実行して成功。artifact に provenance、checksum、検証結果 |
| 3 テスト | test と smoke job | Rosetta で全 suite(失敗は原因を分類)、26 Intel で起動 |
| 4 パッケージ | `install.sh`、署名、配置 | 実機で起動、権限付与、再インストール後も権限維持 |
| 5 実機検証 | 次節の項目と、実行時の修正 | Acceptance を満たすか、未達を明示 |
| 6 更新手順 | 更新通知、rollback の演習、手順の文書化 | 旧版と設定に戻せる |

push と Actions 実行は段階 1 で必要になる。commit と push は都度の指示に従う。

### 段階 0 の結果(2026-11-27、macOS 26.7.3(25G342)の実機)

ローカルの Swift 6.3.3 で作った使い捨てのプログラムによる確認で、固定 toolchain と OmniWM 本体は使っていない。

**(a) Metal**(64×64 の最小再現。本体と同じ format と usage。CAShapeLayer などを含む実シェーダーは未実行)

| 条件 | UHD 630 | Radeon Pro 5600M(既定デバイス) |
|---|---|---|
| shared、API validation 有効 | assert で abort | assert で abort |
| shared、upload して GPU が読む(validation 無効) | 正常 | 正常 |
| shared、IOSurface に描画(validation 無効) | 正常 | 正常 |
| shared、描画して `getBytes`(validation 無効) | 正常 | **0 が返る(3 回とも)** |
| managed(`getBytes` は blit `synchronize` あり) | 正常 | 正常 |

- 両 GPU とも非 Apple family。validation のメッセージは「Shared は texture に使えない」「IOSurface texture は Managed 必須」。
- **修正が必要**: 非 Apple family では 5 箇所を `.managed` にし、`ShellLayerRaster` と `ShellSettingsPreviewRaster` は `getBytes` の前に `synchronize` する。IOSurface の描画は `synchronize` なしでも読めた。

**(b) SMC**: 上流の `ShellSMC` はこの機体で温度キーを 0 件返す(温度は空欄になる)。全キーが `sp78`(2 バイト)で、上流の読み出しは 4 バイトを要求する。CPU コアは `TC1C`〜`TC8C`(ほかに `TC0P`、`TCXC`)、GPU は `TG0P`、`TG1P`、`TGDD` など。`TGDS` は -127、`TGDT` は 0 を返すので範囲外として除外が要る。**修正が必要**(D5)。

**(c) Hidden Bar**: macOS 26.7.3 にも Hidden Bar が参照する preference `trackedApplications` が存在し、外形が辞書の配列(偶数個)であることだけを確認した。読めたのは実験を実行したターミナルの権限による。上流の形式検証、OmniWM での有効化、非表示の動作、書き込みは**未確認**で、上流の Hidden Bar が 26 で正常に動作するかは分からない。合意どおり D2 の macOS 27 ゲートを入れる。

### 段階 1 の結果(Gate 1 合格、2026-11-27 承認)

| 項目 | 内容 |
|---|---|
| commit | docs `325c9021334347ee3628bf1ad7809d7793269bf7`、build `93c486dad46abe77449cc01f3b8b99ce3feee19a`(bookmark `intel/v0.8.0`) |
| run | [OmniWM Intel #45871497400](https://github.com/shuheykoyama/OmniWM/actions/runs/45871497400)。初回で成功。job 12 分 20 秒(うち Ghostty のビルド 9 分 29 秒) |
| runner | macOS 27.0.1(26A434)arm64、3 CPU、7 GiB、Xcode 27.0(27A266a)、SDK 27.0、Metal 32023.921。空き容量は 37 GiB から 33 GiB。メモリのピークは未計測 |
| source | Ghostty `8265e74cd3945c56d59aba3917ff01af33ba3e78`(`1.4.1-main+8265e74`)、Zig 0.17.0、`-Doptimize=ReleaseFast -Demit-macos-app=false -Dxcframework-target=universal` |
| digest | framework zip `b9ba90fc983ca1e220a69cf9ae73d48e055fe457d648bc9b4a240571f30797b5`、`libghostty-internal.a` `b401b728e8c89a3e8eff241f4c8f31b6d405c1dc34fbb5345cd89a7ee7ea78a5` |
| artifact | `ghosttykit-x86_64`(ID 15143059589、`sha256:b04acd41b04080c4101af3021a21c27370536c7c75cc0115885581254db9f366`、期限 2026-12-26) |
| 検証 | 276 member すべて x86_64、最低 OS 13.0。header は固定ソースと公式 zip に一致。x86_64 の最小リンク成功。実機で同じ検査を再実行して一致し、確認用プログラムも実行できた |

残存リスク: `ghostty_init` 以降の実行時動作は未確認。cache hit の経路と複数回ビルドの再現性は未確認。Lix の導入で self-test の警告が 1 件出た(後続は成功)。`workflow_dispatch` は default branch にないため使えない。

### 段階 2 の補足
- `OMNIWM_ARCH`(未指定は `arm64`)を `Package.swift`、`ghostty-preflight.sh`、`package-app.sh`、`Makefile`、`localization.py` が参照する。x86_64 の archive digest は `OMNIWM_GHOSTTY_X86_64_ARCHIVE_SHA256` で渡す。
- 公式の v0.8.0 `.app` は比較の基準としてだけ取得し、digest を固定する。上流の新版を取り込むときは `verify_app.py` に digest を追加する。
- 固定 toolchain は、`swift -print-target-info` の resource path が `.swift-version` の日付を含むことで確認する。
- 実行時の互換修正(Metal、SMC、Hidden Bar)は含めない。

---

## 8. テスト計画(Acceptance Criteria 対応)

| Criteria | 確認方法 |
|---|---|
| 再現可能な x86_64 ビルド、依存の arch、最低 OS | CI を 2 回実行して provenance と版を照合(bit 単位の一致は求めない)。`verify_app.py` |
| 実機で起動、SIP 有効のまま権限付与 | `csrutil status`、初回の権限画面 |
| コア機能、Quake、OmniShell、設定、CLI、IPC | 手動チェックリスト: Niri / Dwindle の操作、ワークスペース、3 画面と抜き差し、アプリ内外のフォーカス切替、ジェスチャー、ルール、ショートカット、sleep / wake、Quake の入力・IME・clipboard・複数画面、全ウィジェット、`omniwmctl` の主要 command / query / subscribe |
| Metal Effects を個別に記録 | `ShellShader*`、`ShellGroupEffect*`、`ShellSettingsPreview*Raster*` の test を実機で実行し、同梱と `styles/` の全 shader を効果 × GPU × 画面で表にする。frame 時間、log、内蔵と外部画面 |
| AI のフォールバック、非対応機能の安全性 | Report Issue の手動送信。Hidden Bar を UI、`enabled = true` の設定、hotkey、IPC から要求してもクラッシュせず設定が保たれる |
| 更新通知、公式版の非インストール、手動更新と rollback | `updaterLastCheckedAt` を消した起動と手動確認。実際の新版がない間は unit test で代替。Install と brew の表示がないこと。段階 6 |
| 未検証の明示 | 結果表で「ビルド成功」「CI テスト」「実機確認」を分ける。26.0 全体は保証しない |

### 残存リスク
- 上流の macOS 26 打ち切り。追従はその直前の版で止まる。
- private API は OS の小更新で変わる。検証は実機の版のみ。
- `xcode-27` の Xcode 差し替え、snapshot の配布終了、Intel runner の廃止(時期の告知は見つからなかった)。
- `MTLCreateSystemDefaultDevice` が dGPU を選ぶことによる電力と発熱。外部画面接続時は影響が小さい見込み。まず計測する。
- 性能依存のテストが Intel や Rosetta で失敗する。閾値は変えず、分類して記録する。

---

## 9. 判断事項(推奨案を採用)

| ID | 事項 | 採用 |
|---|---|---|
| D1 | 実機が 26.7.3 で、仕様書の当初の記載 26.5.2 と違う | 仕様書の検証 OS を 26.7.3 に改訂する |
| D2 | Hidden Bar は docs が 27 要件、コードが未ゲート。「27 要件を維持」と「同じ OS の公式版と比較」が食い違う可能性 | 27 のゲートを追加する(26 でのシステム設定の書き換えが起こる可能性を塞ぐ) |
| D3 | 署名の identity | 専用の自己署名「OmniWM Intel」。代案は既存の Apple Development(期限あり) |
| D4 | Bundle ID と名称 | 上流と同じ |
| D5 | Intel の温度センサー対応(追加の互換実装) | 実施する |
| D6 | `brew` 案内の非表示と文言変更 | 実施する |
| D7 | Report Issue の送信先は上流のまま | コードは変えず、Intel 固有の不具合は上流に送らない |
| D8 | fork への branch push と Actions 実行 | 段階 1 で許可を得る |
| D9 | 上流が 26 を打ち切ったら最後の対応版で固定する | Update Policy に追記する |

### agent instructions の提案
リポジトリに `AGENTS.md` はなく、`.gitignore` が `/AGENTS.md` と `/CLAUDE.md` を除外している。追加するならローカル専用で次の内容にする。
- `docs/intel-compatibility.md` が仕様、`docs/intel-build-plan.md` が計画。
- arm64 の既定動作と上流ファイルの差分を最小にする。
- アプリのビルドは CI(ローカルは Intel で 27 SDK がない)。VCS は `jj`、GitHub は `gh`。
- 上流の `CONTRIBUTING.md` は追加のソースコメントを避ける方針。個人設定の行ごとコメントより上流規約を優先するかは確認のうえ決める。
