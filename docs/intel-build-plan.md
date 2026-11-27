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
3. **test-build / test / test-report**: x86_64 の test bundle を一度だけ作り、上流の分割で Rosetta 上の 4 shard を実行して集計する(詳細は段階 3 の設計)。
4. **test-native**: `macos-26-intel` で同じ bundle を runner の XCTest で実行する。調査であり、合否に含めない【実験】。
5. **arm64 の非回帰**: 既存の `ci.yml` を対象 branch に手動実行する。
6. **app-smoke**: `macos-26-intel` で app job の artifact を使い、`omniwmctl --help` と短時間の起動を確認する。補助の検証で、合否に含めない。

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
| 3 テスト | test-build、test、test-report、test-native job | Gate 3: 固定 toolchain の x86_64 テストビルドが成功し、x86_64 のコードが実行されたことを確認。CI 対象の全テストと Updater の制約テストが成功。arm64 の 8,792 件との差を説明。未実施、スキップ、タイムアウト、失敗を区別。ネイティブ実行は調査 |
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

### 段階 2 の結果(Gate 2 合格、2026-11-27)

対象 commit は `7d8b86c037fb2df422a6f0558b1edda26674b515`(`intel/v0.8.0`)。`40f311f884`(Updater)、`a83b7eed0a`(arch 切り替えと app job)、`7d8b86c037`(文書)の 3 件。

| run | 結果 |
|---|---|
| [OmniWM Intel #45909077043](https://github.com/shuheykoyama/OmniWM/actions/runs/45909077043) | 成功。run 27 分 10 秒(GhosttyKit job 45 秒、app job 26 分 18 秒) |
| [OmniWM CI #45909079292](https://github.com/shuheykoyama/OmniWM/actions/runs/45909079292)(arm64、手動実行) | 2 回目の試行で成功。1 回目は `Tests (shard 2)` が toolchain のダウンロード中の `Connection reset by peer` で失敗(テストは 0 件実行)。失敗 job だけを同じ commit で再実行した |

| 項目 | 内容 |
|---|---|
| x86_64 ビルド | Swift 6.5-dev(`swift-DEVELOPMENT-SNAPSHOT-2026-11-01-a`)、Xcode 27.0、SDK 27.0。release build 783 秒。localization 検査も成功。runner の空き容量は 35 GiB から 30 GiB、メモリは未計測 |
| GhosttyKit | cache hit(Lix、gettext、ビルドの step はスキップ)。検査 6 項目は ghostty job と app job の両方で実行して合格し、Gate 1 の結果と一致 |
| `.app` | 352 ファイルと `Info.plist`(git hash を除く)が公式版と一致。Mach-O 3 つはすべて x86_64、最低 OS 26.0。依存ライブラリ(75 / 13 / 4 件)、weak の指定、run path に公式版との差異なし |
| 署名 | ad-hoc、entitlements 一致。ネストした Mach-O を個別に検証。zip 展開後の全ファイルの内容と権限が一致 |
| 実機での静的確認 | macOS 26.7.3 で再検査して CI と一致。参照する必須シンボルの欠落は 0 件。欠ける weak シンボル 7 件は公式版も weak で参照。アプリは起動していない |
| Updater | arm64 CI で関連 31 件が成功。x86_64 はローカルの Swift 6.3.3 と設定ストアの stub で 24 件が成功(固定 toolchain では未実行) |
| arm64 非回帰 | 整形、lint、IPC 生成物、開発ツールテスト、ビルド、localization、ARC audit が成功。Swift テスト 8,792 件(shard 1〜4: 2,005 / 2,411 / 2,264 / 2,112)、失敗 0、スキップ 0 |
| cache | 再実行の shard 2 は、app job が同じ branch に保存した Swift toolchain の cache を使い、ダウンロードしなかった |
| artifact | `omniwm-x86_64`(ID 15160023451、期限 2026-12-26)。app zip の SHA-256 `a60681746958bfb1472b4128969b5bb3997f8e4f2cef9b77af0a2ccf89e9142d`、artifact digest `sha256:d93c0e3315b3a8bc31922ff67673c224f4ef07d3bb1f524e718566c63ee02c74`。provenance に source commit、toolchain、Ghostty の revision と digest、公式版の digest を記録 |

既知の残存リスク:
- Intel の release build で `warning: input verification failed` が 843 件出る(未使用の clang 引数の警告もある)。調査は「`input verification failed` の調査」を参照。現段階では修正も抑制もしない。
- 実機依存のテスト(`OMNIWM_LIVE_CHECKS`、静的な数え上げで 78 メソッド)は、上流の設計で CI ではコンパイルのみ。実行していない。
- x86_64 版は起動と全テストが未実行。macOS 26.0 は未確認で、シンボルの照合は 26.7.3 だけ。
- 段階 0 の Metal、SMC、Hidden Bar の問題は未修正。署名は ad-hoc のまま。
- 上流の `ci.yml` は cache が読み取り専用で、fork には `main` で作る cache がない。同じ branch に toolchain の cache がなければ各 job がダウンロードし、ネットワーク障害の影響を受ける。

### 段階 3 の設計

2 回目の実行から、テストは SwiftPM を介さず `xctest` で直接実行する(経緯は後述の 1 回目の実行と probe)。

| job | runner | 内容 |
|---|---|---|
| test-build | `xcode-27` | GhosttyKit を再検証し、固定 toolchain で `swift build --build-tests --arch x86_64`。生成した Mach-O がすべて x86_64 であることを確認し(最低 OS と OS 外の依存は記録)、一覧取得より先に bundle を artifact に保存する。その後に Rosetta を確認し、一覧ツールを固定 toolchain でビルドして一覧を作り、分割を検証する。`OMNIWM_LIVE_CHECKS` はコンパイルのみ |
| test(4 shard) | `xcode-27` | 上流の `ci_test_plan.py` の分割を使い、`arch -x86_64` の `xctest -XCTest` で実行する。fish と Nushell が要る shard は上流と同じ手順 |
| test-native(4 shard) | `macos-26-intel` | 同じ bundle、一覧、分割を Xcode 26.6 の `xctest` で実行する。固定 toolchain、fish、Nushell は導入しない。合否に含めないが、製品の不具合は判定への影響を報告する |
| test-report | `ubuntu-latest` | `test_report.py` が両環境を別々に集計する。判定は Rosetta で、一覧にある全テストの成功と Updater の 4 suite の存在を要求し、失敗、未終了(クラッシュやタイムアウト)、スキップ、未実行を分けて報告する |
| app | `xcode-27` | 既存の確認に加え、配布する Mach-O と UUID が一致する dSYM を `omniwm-x86_64-dsyms`(zip と `symbols.json`: source commit、arch、UUID、hash)に保存する。app には同梱しない |
| app-smoke | `macos-26-intel` | app job の zip を checksum と署名で確認して展開し、`omniwmctl --help` を実行する。アプリは直接実行と LaunchServices 経由の 2 通りで各 20 秒起動する。生存、dyld のエラー、crash report、表示ウィンドウ、`SIGTERM` での終了、残プロセスを記録する。権限は付与せず、SIP と Gatekeeper は変えない。結果は合否に含めないが、製品の不具合は判定への影響を報告する |

SwiftPM の再現は、固定版の [TestingSupport.swift](https://github.com/swiftlang/swift-package-manager/blob/swift-DEVELOPMENT-SNAPSHOT-2026-11-01-a/Sources/Commands/Utilities/TestingSupport.swift) と [SwiftTestCommand.swift](https://github.com/swiftlang/swift-package-manager/blob/swift-DEVELOPMENT-SNAPSHOT-2026-11-01-a/Sources/Commands/SwiftTestCommand.swift) に合わせる。

- 起動は `xcrun` の `xctest -XCTest <識別子のカンマ区切り> <bundle>`。`--filter` は識別子 `Module.Class/test` への正規表現検索に一致するもの、`--skip` は一致しないもの。
- 環境変数は順に、`NO_COLOR=1`、`DYLD_LIBRARY_PATH` に bundle の親ディレクトリ、`SWIFT_TESTING_XCTEST_INTEROP_MODE=complete`(tools version 6.4 以上。一覧取得では設定しない)、固定 toolchain の `usr/lib/swift/macosx/testing`、platform の `Library/Frameworks`、`Library/PrivateFrameworks`、`usr/lib`、`SWIFT_TESTING_ENABLED=0`。
- 一覧は `Scripts/intel/list_tests.swift` が `XCTestSuite.default` から取る。private やネストしたテストクラスは XCTest と SwiftPM で表記が異なる(ローカルで確認)。OmniWM の arm64 の結果にはないので、現れた場合は停止する。
- 引数と環境変数の合計が `ARG_MAX`(1 MiB)を超えると起動できない。shard ごとに大きさを記録し、超える場合は停止する。`xctest` は存在しない識別子を黙って無視するので、実行結果を一覧と照合する。
- Swift Testing は直接実行の対象外になる。テストの source に `import Testing` があるか、bundle に `__swift5_tests` section があれば停止する(source には現在ない)。

追加の確認:
- `rosetta.sh` は Rosetta で動くことと `xctest` の x86_64 を要求し、`swiftpm-xctest-helper` の architecture は記録だけする。
- 分割は一覧に対して検証し、全グループの和が一覧と一致し、重複と空のグループがないことを要求する。
- 各 shard は、展開した Mach-O が test-build の成果物と同一の x86_64 であることを実行前後に確認する。
- 集計はテスト名で照合し、未実行、一覧にないテスト、重複実行、一覧の重複も不合格にする。arm64 との照合は実行後に両者のログで行う。
- 比較基準は commit `7d8b86c037` の arm64 CI(run 45909079292、8,792 件)。以後、`Sources`、`Tests/OmniWMTests`、`Package.swift`、分割の定義は変えていない。テスト内の arch 分岐は `testSelfUpdateSupportFollowsTheBuildArchitecture` の本体だけなので、期待するテスト名は同じ。

| 検証できること | Rosetta(macOS 27) | ネイティブ(`macos-26-intel`) |
|---|---|---|
| x86_64 のコード生成と ABI、arch 分岐 | できる(命令は変換されて実行) | できる |
| macOS 26 の framework、private API、XCTest との互換性 | できない | 部分的(26.6 の runner。26.7.3 や 26.0 ではない) |
| Intel と AMD の GPU(Metal の shared 問題など) | できない(GPU は Apple 系) | 仮想 GPU の範囲。実機の GPU は段階 5 |
| 速度に依存する挙動 | 変換の負荷で実機と異なる | 仮想マシンの性能で実機と異なる |

### `input verification failed` の調査

- **出力元**: `dsymutil` の入力 DWARF の検証。失敗すると警告を出して処理を続ける([LLVM のソース](https://github.com/llvm/llvm-project/blob/main/llvm/tools/dsymutil/DwarfLinkerForBinary.cpp))。
- **件数が違う理由**: 警告は release の実行ファイルをリンクして dSYM を作るときに出る。Intel の app build(843 件、重複を除く object は 812)と、上流 `main` で追加された `script-arc-audit`(release の product)には該当する step がある。fork の v0.8.0 の arm64 CI は debug build と、library target だけの release compile なので 0 件。
- **ローカルでの再現**(Apple LLVM 21 / Swift 6.3.3。固定 toolchain ではない): `OmniWMShellCore` を `-g -O` で作ると、x86_64 と arm64 の両方で同じ警告になる。検出されるのは `.debug_names` の索引に、コンパイラが生成する基本型 `DW_ATE_unsigned_*` の項目がないという内容だけ。C++(Luau)の object でも同種。
- **機械語とテストへの影響**: `dsymutil` はリンク済みのバイナリからデバッグ情報を集める工程で、機械語を変えない。テストは debug build で dSYM を作らないため、結果に影響しない。
- **デバッグ情報とクラッシュ解析への影響**: 同じ入力で、入力検証の有無により dSYM の DWARF は変わらず、`dsymutil` は成功した。索引に項目がないため、索引を使う名前検索で該当の基本型が見つからない可能性はある。関数名や行番号の解決への影響はないと推定するが、実際の crash report では確認していない。これとは別に、配布する artifact は dSYM を含まないので、この版の crash report をソース行まで解析するには dSYM の保存が要る。
- **残る確認**: 固定 toolchain の object での内容は、app job の `dwarf-findings.txt`(target ごとに 1 object)で確認する。

### 段階 3 の 1 回目の実行(Gate 3 未合格、2026-11-27)

commit `7818cdd575`(文書)と `85bf4575caa2c4aacace62a8acf607225465532e`(CI)。[OmniWM Intel #45916708282](https://github.com/shuheykoyama/OmniWM/actions/runs/45916708282)、09:26〜10:11 JST。

| job | 結果 | 時間 |
|---|---|---|
| ghostty | 成功(cache hit、再検証 6 項目) | 42 秒 |
| app | 成功。`verify_app.py` 合格 | 41 分 42 秒(前回 26 分 18 秒。製品コードは同じで、各 build が長くなった) |
| test-build | **失敗**(`Check Rosetta`)。ビルドの前で停止 | 2 分 5 秒 |
| test(Rosetta)、test-native | 依存のため未実行 | なし |
| test-report | 失敗(テスト一覧がない) | 8 秒 |
| app-smoke | 成功 | 1 分 55 秒 |

x86_64 のテストはビルドも実行もされていない(0 件)。分類はテスト基盤の互換性の問題で、テストの失敗、runner 障害、タイムアウトではない。

**原因**: 固定の swift.org toolchain に `swiftpm-xctest-helper` がなく、代わりに使われる Xcode 27 の同名ファイルは arm64 専用。固定版(`swift-DEVELOPMENT-SNAPSHOT-2026-11-01-a`)の SwiftPM は `swift test list`、`--filter`、`--skip` でこれを呼び、絞り込みのない実行では呼ばない。`swift`、`swift-package` は universal。Xcode 27 の `xctest` の architecture は、検査がその前で止まったため未確認。

**対応案**(後に承認): SwiftPM の代わりに、上流の分割を保って `xctest` を直接実行する。固定 toolchain で作る小さな一覧ツールで x86_64 のテスト一覧を取り、上流の `--filter` / `--skip` の正規表現を SwiftPM と同じ意味で適用して `-XCTest` に渡す。SwiftPM が設定する `SWIFT_TESTING_ENABLED=0`、`NO_COLOR`、platform の `DYLD_FRAMEWORK_PATH` と `DYLD_LIBRARY_PATH` を再現する。一覧ツールと `-XCTest` の指定は、ローカルの Xcode 26.6 と updater の 24 テストで一致を確認した。`xctest` に x86_64 の slice がなければ、Rosetta の経路は Xcode 27 では成り立たない。

**app-smoke**(`macos-26-intel`、macOS 26.6.1、x86_64。runner image は SIP が無効で、こちらでは変更していない。Gatekeeper は有効、quarantine 属性なし):

| 確認 | 結果 |
|---|---|
| `omniwmctl --help` | 終了コード 0、182 行 |
| 直接実行 | 20 秒生存。welcome 画面とバーのウィンドウを表示(呼び出し元の権限を引き継いだと推定)。`SIGTERM` で終了 |
| LaunchServices 経由 | 20 秒生存。権限画面 `OmniWM Permissions` だけを表示。`SIGTERM` で終了 |
| dyld、crash report、残プロセス | いずれもなし |
| システムログ | OmniWM 自身の error はなし。error 行は macOS のサービス由来 |

**判定(承認済み)**: Intel ネイティブ環境でのアプリと CLI の簡易起動は合格。Gate 3 は保留。

起動と初期化の範囲の確認で、ウィンドウ管理、Quake Terminal、シェーダーの描画、実機の GPU は含まない。runner は SIP が無効なので、**手元の SIP 有効の環境での挙動は未確認**。

**固定 toolchain の `dwarf-findings.txt`**: target ごとの 12 object のうち 3 object に error があり、内容は `.debug_names` の `DW_ATE_unsigned_*` の索引漏れだけ(9 行)。ほかに `.debug_line` のファイル名重複の warning(7 行)。警告は 843 件で前回と同数。全 object は調べていない。

artifact: `omniwm-x86_64`(ID 15164078797、app zip の SHA-256 `7178ae92c0d889ea9144acc98df89b8625ef43e6e923bb5730e27865d354f257`)、`intel-app-smoke`(ID 15163623748)。

警告の調査はこの範囲で終える。抑制も toolchain の変更もしない。

**dSYM の再利用**: ローカル(Swift 6.3.3)の `swiftbuild` の release build は、実行ファイルの隣に UUID が一致する `<名前>.dSYM` を残した。実機デバッグに備えるには、app job の `.build/out/Products/Release/*.dSYM` を artifact に加えれば足りる見込みで、新しい管理の仕組みは作らない。固定 toolchain の CI での存在と UUID の一致は、追加するときに確認する。

### XCTest の互換性 probe(Step 1)

`intel-probe.yml` は 3 件だけのテストを持つ小さな package(`Scripts/intel/probe/`)を使い、GhosttyKit とアプリをビルドしない。

| job | runner | 内容 |
|---|---|---|
| build | `xcode-27` | 固定 toolchain で x86_64 の test bundle を作り、arch を検査して保存。実行環境の確認はしない |
| rosetta | `xcode-27` | `xctest`、XCTest の framework とライブラリ、toolchain の各ツールの architecture を記録。`xctest` の直接実行(環境変数なし、SwiftPM と同じ環境、テストの指定)と、参考として `swift test` の絞り込みなし実行と `test list` |
| native | `macos-26-intel` | 同じ bundle を Xcode 26 の `xctest` で直接実行 |

直接実行の成立は、SwiftPM と同じ環境での全テストと、指定した 1 テストの成功で判定する。テストは x86_64 のコードであることと、Rosetta かどうかを出力する。

probe の間だけ、`intel.yml` の `paths-ignore` に probe 関係のパスを加えて本体の job を起動しなかった(2 回目の実行で解除)。

### probe の結果(2026-11-27、commit `2c90e148`、[run 45922523190](https://github.com/shuheykoyama/OmniWM/actions/runs/45922523190))

3 job とも成功。同じ bundle(x86_64、最低 OS 26.0)を両環境で実行した。

| 確認 | Rosetta(macOS 27.0.1、Xcode 27.0) | ネイティブ(macOS 26.6.1、Xcode 26.6) |
|---|---|---|
| `xctest`、XCTest の framework と `libXCTestSwiftSupport.dylib` | x86_64 あり | x86_64 あり |
| Xcode の `swiftpm-xctest-helper` | **arm64 のみ** | x86_64 あり |
| `xctest` の直接実行(環境変数なし / SwiftPM と同じ環境) | 3/3 / 3/3 | 3/3 / 3/3 |
| `-XCTest` でのテスト指定 | 1/1 | 1/1 |
| `swift test`(絞り込みなし) | 3/3 | 対象外 |
| `swift test list` | **失敗**: helper が「現在の architecture の版がない」として bundle を読み込めない | 対象外 |
| テストが出力した `translated` | 1 | 0 |

- 固定 toolchain の `swift`、`swift-test`、`swift-package` は x86_64 と arm64 で、`swiftpm-xctest-helper` は含まれない。
- `Testing.framework` の arch は個別に記録していない(bundle の読み込みは両環境で成功)。
- Rosetta の job では、最初の `swift test` がテストを始めるまで約 5 分半かかった(直前の直接実行は数秒)。原因は未確認。
- probe で再現した環境変数は 4 つで、固定版の SwiftPM より少なかった(段階 3 の設計を参照)。

**判定(承認済み)**: 直接実行方式を採用する。Rosetta を Gate 3 の必須条件、ネイティブ Intel を補助検証とする。

### 段階 3 の 2 回目の実行(Gate 3 未合格、2026-11-27)

commit `24461ae701214837f854ad1da8457343dce81d86`。[OmniWM Intel #45950073778](https://github.com/shuheykoyama/OmniWM/actions/runs/45950073778)、約 39 分。ghostty、app、test-build(21 分 32 秒)、app-smoke は成功、テストは両環境の全 shard が失敗。

| 項目 | Rosetta(macOS 27.0.1、Xcode 27.0) | ネイティブ(macOS 26.6.1、Xcode 26.6) |
|---|---|---|
| 成功 / 失敗 / スキップ / 未実行 | 8,777 / 15 / 0 / 0 | 0 / 0 / 0 / 8,792(bundle を読み込めない) |
| shard の実行時間 | 354、426、799、490 秒(arm64 は 298、253、442、283 秒) | 各 4〜5 秒で失敗 |
| 固定 toolchain の testing ライブラリ、fish、Nushell | あり | なし |

- テスト識別子は arm64 の 8,792 件と完全に一致(片側だけの識別子は 0)。shard の件数も同じ。`import Testing` と `__swift5_tests` はない。引数は最大 242,757 byte(上限 1,048,576)。Updater の 4 suite は 31 件すべて成功。
- 失敗した 15 件は arm64 では成功している。

| 失敗 | 件数 | 観察 | 原因 |
|---|---|---|---|
| `ShellGlassSceneTests`(74 件中 10)、`ShellVitreusBarTests`(79 件中 2)、`ShellVitreusFocusPolishTests`(7 件中 1) | 13 | glass の縁、gradient、glow などの画素が 0 または期待と異なる | 未特定。Rosetta、x86_64、製品の不具合を区別できていない |
| `ShellPulseKitTests.testNativeSpringsMatchTheRecipeDynamicsForEveryKitSpring` | 1 | `stiffness` の完全一致の比較で末尾の桁が異なる(damping は許容誤差つきの比較) | arch による浮動小数点演算の差と推定 |
| `HostedWindowManagementTests.testFloatPlaceNativeResizeAndRetilePreserveIndependentSessions` | 1 | 描画面の文字列に期待する文がない | 未特定 |

**ネイティブの読み込み失敗**: `_swift_task_isCancelledWithFlags` が macOS 26 の `libswift_Concurrency` にない(ローカルの 26.7.3 にもない)。参照元は、`Task` の値の `isCancelled` を読むテストコードの closure 16 箇所だけ。呼び出しは `_stdlib_isOSVersionAtLeast(27, 0, 0)` で保護されているが、import が weak でないため読み込み時に失敗する(固定 toolchain の挙動と推定)。test bundle の Swift runtime の strong import 2,105 件で欠けているのはこの 1 件(ほかの framework は未確認)。配布する 3 つの Mach-O は参照していない。製品コードが同じ書き方をすると macOS 26 で起動できなくなり得る(app-smoke で検出する)。

**dSYM**: 配布する 3 つの Mach-O すべてで UUID が一致(CI と、取得した artifact のローカル照合)。app には含まれない。`dwarfdump --lookup` はソース行を解決した(1 件で確認)。ローカルの Xcode 26.6 の `atos` はこの dSYM で異常終了した(原因未確認)。警告は 843 件、固定 toolchain の object の内容は前回と同じ。

artifact: `omniwm-x86_64`(15178505271、app zip の SHA-256 `4097e69f8861f4103888f72a24e65cff5fe73642c54cb3771c38de3849805199`)、`omniwm-x86_64-dsyms`(15178330872、zip の SHA-256 `8a514f11028b02156a2879abfbaf76b5194143b70ff324bf434599206d14f4b3`)、`intel-test-list`(15177636234)、`intel-test-summary`(15178181601)、`intel-app-smoke`(15177274349)、`swift-tests-x86_64`(15176589058、2026-11-30 まで)。

**残存リスク(今回は追加対応しない)**: Xcode 26.6 の `atos` が、この build の dSYM で異常終了する。`dwarfdump --lookup` はソース行を解決した。

### 弱リンクの probe(承認済み)

固定版の Swift では、`Task` の値の `isCancelled` が `@_transparent` で呼び出し側に展開され、`#available(anyAppleOS 27, *)` の中で `swift_task_isCancelledWithFlags` を呼ぶ([TaskCancellation.swift](https://github.com/swiftlang/swift/blob/swift-DEVELOPMENT-SNAPSHOT-2026-11-01-a/stdlib/public/Concurrency/TaskCancellation.swift))。呼ばれる関数の availability は `StdlibDeploymentTarget` で書かれ、この macro は標準ライブラリを作る環境より新しい版にならない([availability-macros.def](https://github.com/swiftlang/swift/blob/swift-DEVELOPMENT-SNAPSHOT-2026-11-01-a/utils/availability-macros.def))。展開された値が利用側で weak import にならない原因と推定し、probe で interface の宣言を確認する。

`intel-cancellation-probe.yml` は `Scripts/intel/cancellation-probe/`(テスト 4 件、名前は本体と同じ `OmniWMTests`)を、指定なし、`-weak-lswift_Concurrency`、`-weak_library` の 3 通りで x86_64 向けにビルドする。import の属性と load command を指定なしと比べ、同じ bundle を Rosetta と `macos-26-intel` で本体の `run_tests.py` により実行する。テストは、キャンセルの前後の `isCancelled`(task の値、静的プロパティ、`UnsafeCurrentTask`)と、子タスクと `sleep` への伝播を確認する。指定なしがネイティブで読み込めないことは想定内として記録する。

製品のリンク設定、テスト、本体の test build は変えない。本体への適用は結果を報告してから判断する。probe の間だけ、`intel.yml` の `paths-ignore` に probe 関係のパスを加える。

### 弱リンクの probe の結果(2026-11-27、commit `f4d3ae8af3`、[run 45957883859](https://github.com/shuheykoyama/OmniWM/actions/runs/45957883859))

**原因**: ビルドは固定 toolchain の `_Concurrency` module を読み込む(SDK のものではない)。その public interface は `_taskIsCancelledWithFlags` を `@available(macOS 13.0, *)` と宣言している(macOS 27 の SDK の interface は `anyAppleOS 27.0`。読み込まれる private interface の宣言は直接は確認していない)。このため deployment target 26.0 では strong import になる。呼び出し自体は macOS 27 の判定の後にある(probe の 5 箇所と本体の bundle の 16 箇所すべて)。

| 指定 | 対象 symbol | `libswift_Concurrency` の import | load command | Rosetta(27.0.1) | ネイティブ(26.6.1) |
|---|---|---|---|---|---|
| なし | strong | 48 件すべて strong | `LC_LOAD_DYLIB` | 4/4 | 読み込み失敗(同じ symbol) |
| `-weak-lswift_Concurrency` | weak | 48 件すべて weak | `LC_LOAD_WEAK_DYLIB` | 4/4 | 4/4 |
| `-weak_library` | weak | 48 件すべて weak | `LC_LOAD_WEAK_DYLIB` | 4/4 | 4/4 |

- 2 つの指定は同じ結果で、ほかのライブラリの import 属性、import の増減は指定なしから変わらない。
- ライブラリ全体が weak になるため、将来ほかの symbol が欠けても読み込み時には失敗せず、呼び出した時点で異常終了する。本体の test bundle では 143 件が weak になる。このうち macOS 26.7.3 に欠けているのは対象の 1 件だけ。
- `-Xlinker` はそのビルドで行うすべてのリンクに適用される(test build の中の実行ファイルと dylib を含む。配布する app は別の job でビルドする)。
- linker に symbol 単位で weak にする指定は見つからなかった(Xcode 26.6 の `man ld` の `-weak*` はライブラリか framework 単位。例外の `-weak_reference_mismatches` は、weak import を宣言する別の object が必要になる)。

### 再現性の確認(run 45950073778 の 2 回目)

失敗した job だけを再実行した。test bundle は同じ artifact で、展開した Mach-O の hash も 1 回目と一致する。失敗した shard は shard 内の全テストが再実行される(Rosetta は全 4 shard なので 8,792 件)。

| 分類 | 件数 | テスト |
|---|---|---|
| 2 回とも失敗し、assertion と実測値が同じ | 7 | `ShellGlassSceneTests` の `testBandJoinsRunningMotion…`、`testGlassTintPlane…`、`testGradientBodies…`、`testVariableAlphaMask…`、`ShellVitreusFocusPolishTests`、spring、ウィンドウの再タイル |
| 2 回とも失敗し、assertion の集合か実測値が異なる | 8 | `ShellGlassSceneTests` の `testClippedComposition…`、`testComposedMaterialTransitions…`、`testCompositionPreserves…`、`testFlatRim…`、`testGlassRim…`、`testRimChanges…`、`ShellVitreusBarTests` の 2 件 |
| 2 回目だけ失敗 | 1 | `ShellGlassSceneTests.testGradientMaterialFollowsCoverageAndRecolorsInPlace`(描かれた画素が 0) |

- 2 回目は 8,776 件成功、16 件失敗、スキップ、未終了、未実行は 0。ネイティブの 4 shard は同じ symbol で読み込みに失敗した。
- **描画の 14 件**: Shader、Raster、GroupEffect、SettingsPreview の 81 件は成功し、画素を読む glass のテストも多くは成功する(静的な判定で約 48 件)。失敗は gradient と rim の描画に集まり、実行ごとに値が変わるものがあるので、時間に依存する要因を疑う(未検証)。段階 0 では Radeon で shared の texture を `getBytes` すると 0 が返った。テストが使う `ShellLayerRaster` も shared と `getBytes` で、症状は似るが、runner の GPU と実機は異なり、同じ原因とする根拠はない。
- **spring**: 14 個中 5 個で、`CASpringAnimation(perceptualDuration:bounce:)` の `stiffness` が `(2π/response)²` と 2〜3 ULP(相対 4.5e-16 以下)異なる。手元の実機(Intel、26.7.3)の小さなプログラムでも同じ 5 個が同じ値になった。Rosetta 固有ではない。
- **ウィンドウの再タイル**: 2 回とも同じ 1 件の assertion(描画面の文字列に入力した文がない)。ログからは 6 回の呼び出しのどれかを特定できない。原因は未特定。

### Step 3: 弱リンクを適用した test build(承認済み)

固定 Swift snapshot に起因する、**テスト基盤に限った workaround**。`Package.swift`、製品コード、テスト、配布する app のリンク設定は変えない。

- `intel.yml` の test build だけで、指定なしのビルドの import を記録してから `-Xlinker -weak-lswift_Concurrency` を足して再リンクする(コンパイルは繰り返さない)。live check のコンパイルにも同じ指定を使う。
- `verify_test_linkage.py` は、すべての Mach-O を検査し、最初の違反で止めずに全製品の差を報告する。要求するのは、import の増減がないこと、weak に変わるのが `libswift_Concurrency` の import だけで残りがないこと、依存の変化が次の 2 つだけであること: このライブラリの `LC_LOAD_DYLIB` が `LC_LOAD_WEAK_DYLIB` になる、このライブラリを何も import しない製品に `LC_LOAD_WEAK_DYLIB` が追加される(承認済み。追加された製品を記録する)。`-dead_strip_dylibs` は使わない。
- ネイティブの job は、macOS 26 が export しない Swift runtime の symbol を一覧にする。
- 両環境で `metal_probe.swift` が、Metal device の名前、GPU family、`hasUnifiedMemory` と、`ShellLayerRaster` と同じ手順(`CARenderer`、marker の command buffer、`getBytes`)の読み戻しを shared と managed で記録する。判定には含めない。
- 製品コードは変わらないので、app job は一時的に手動実行のときだけにする(push では実行しない)。app と app-smoke の結果は commit `24461ae701` のものを使う。

**1 回目の実行(2026-11-27、[run 45961574075](https://github.com/shuheykoyama/OmniWM/actions/runs/45961574075)、commit `ecf0981ef5`)**: test build は固定 toolchain のビルドと再リンクに成功し、検査で停止した。テストは 0 件実行。debug の `OmniWM` と test bundle は検査を通り(製品は名前順に検査する)、3 番目の `libOmniWMNowPlaying.dylib` で「読み込むライブラリが変わった」として失敗した。`omniwmctl` は未検査。このライブラリは Swift Concurrency を使わず、`-weak-l` で `libswift_Concurrency` の weak な読み込みが追加されたと考えられる。ローカル(Swift 6.3.3)の最小の package では、同じ構成の dylib に `LC_LOAD_WEAK_DYLIB` が 1 件増え、import は増えないことを再現した。分類は検査の想定の誤りで、テストの失敗や runner の障害ではない。

**対応(承認済み)**: 上の 2 つ目の依存の変化を許し、それ以外は拒否する。4 製品を持つローカルの package で、本体と同じ手順(ビルド、記録、同じ場所で再リンク、比較)を実際の Mach-O に通した。Concurrency を使う 2 製品は weak な読み込みに変わり、使わない 2 製品には weak な読み込みが追加され、無関係な weak の依存を足した対照は拒否した。

**2 回目の実行(Gate 3 未合格、2026-11-27、[run 45964948475](https://github.com/shuheykoyama/OmniWM/actions/runs/45964948475)、commit `258437ad57`)**: ghostty と test-build は成功、テストは両環境の全 shard が失敗。app と app-smoke は実行していない。

- **弱リンク**: `OmniWM`(128 件)、test bundle(143 件)、`omniwmctl`(40 件)の `libswift_Concurrency` の import が weak になり、`libOmniWMNowPlaying.dylib` は import なしで weak な読み込みが 1 件増えた。違反は 0。対象 symbol は strong から weak。macOS 26.6.1 の runner に欠ける Swift runtime の symbol は、test bundle の対象 1 件だけ(17 ライブラリ)。
- 一覧は 8,792 件で、arm64 の識別子と一致する。

| | Rosetta(27.0.1) | ネイティブ(26.6.1、仮想 GPU、SIP 無効) |
|---|---|---|
| 成功 / 失敗 / 停止 / 未実行 | 8,778 / 14 / 0 / 0 | 3,538 / 11 / 4 / 5,239 |
| Updater の 31 件 | すべて成功 | 21 件成功、`UpdateCoordinatorTests` の 10 件は未実行 |

| 失敗(Rosetta) | 件数 | 分類 |
|---|---|---|
| glass の gradient と rim(Glass 10、VitreusBar の rim 1、FocusPolish 1) | 12 | 下の診断では、mask 付き gradient が storage mode と同期に関係なく空で返る。Rosetta の描画に起因すると推定(arm64 の同じ診断は未実施) |
| `ShellWidgetClickTests.testDrawerPopoutsJoinTheBar…`(新規) | 1 | 変化の通知を待った直後に popout の大きさが 0。非同期の待ち合わせの競合と推定 |
| spring | 1 | x86_64 の演算差(値は実機と同じ) |

| 失敗(ネイティブ) | 件数 | 分類 |
|---|---|---|
| `ShellSettingsPreviewNativeRasterTests` 1、`ShellBoundTrackPixelTests` 4(画素が空、端が見つからない) | 5 | shared の読み戻しが空になる GPU。製品の `.shared` 固定が原因と推定(段階 5 の修正対象) |
| `CASDFKeyFillHighlightEffect.diffuseHeightScale` がない(BoundTrack 1、FrameProbe 1、GlassSurface 1) | 3 | macOS 26 の差。実機の 26.7.3 でも `diffuseHeightScale`、`diffuseAmountScale`、`diffuseSpreadScale` がない。製品は native glass を使わず代替の描画になる |
| 温度の読み取り | 1 | SMC の Apple silicon 用キー(段階 5 の対象)か、仮想機に sensor がないかは区別できない |
| spring | 1 | 同上 |
| `HostedAppAuthoringTests` の描画待ち | 1 | 10 秒の待ちを含むテストが 638 秒かかった。runner の停滞と推定 |
| 停止(70 分で打ち切り) | 4 | `HostedWindowPresentationTests.testDeclaredPresentation…`、`HostedChildPresentationTests.testOverviewExcludesSheets…`、`ShellBundledWidgetTests.testDefaultWidgetsFit…`、`ShellSettingsPreviewTests.testAllInstalledStyles…`。Rosetta では 1〜6 秒で成功。原因未特定 |

- ネイティブでは、alert を作って属性を調べるだけのテストも 1,131 秒と 241 秒かかって成功した。
- 既知の 16 件のうちネイティブで実行されたのは 2 件(再タイルは成功、spring は同じ値で失敗)。Rosetta では再タイル、`testCompositionPreserves…`、`testPieceSnapshots…` が今回成功し、実行ごとに結果が変わる。
- **ウィンドウの再タイル**: 入力欄の内部状態の assertion は成功し、描画面の文字列だけが欠けていた。待ち合わせ(`hostedRender`)は前より新しい描画なら何でも受け付けるので、直前の resize の描画で解ける競合を疑う(未検証)。

**Metal の device と読み戻し**(各 200 回、全回で command buffer は完了):

| 環境 | device | unified | family | 単色 shared / managed | mask 付き gradient shared / managed |
|---|---|---|---|---|---|
| Rosetta | Apple Paravirtual | true | apple1〜5、mac2、common1〜3 | 正常 / 正常 | 空 / 空(1 台の managed は一部正常) |
| ネイティブ runner | Apple Paravirtual | false | common1、common2 | **空** / 正常 | **空** / 正常 |
| 実機 Radeon | AMD Radeon Pro 5600M | false | mac2、common1〜3、metal3 | **空** / 正常 | **空** / 正常 |
| 実機 UHD 630 | Intel | true | mac2、common1〜3、metal3 | 正常 / 正常 | 正常 / 正常 |

いずれも API validation 無効で、GPU の互換性を保証するものではない。

**撤去の条件**: 指定なしのリンクで `_swift_task_isCancelledWithFlags` が strong import でなくなった時(`verify_test_linkage.py` が警告する)。`.swift-version` を更新した時はこの警告を確認し、出ていれば `-Xlinker` の指定、`verify_test_linkage.py` と再リンクの step、`Scripts/intel/cancellation-probe/` と `intel-cancellation-probe.yml` を削除して、macOS 26 で test bundle が読み込めることを確認する。

**実機の読み戻し(2026-11-27、macOS 26.7.3、Xcode 26.6 で作った診断プログラム、validation 無効、各 200 回)**: Radeon Pro 5600M(既定の device)は、shared の texture では単色と mask 付き gradient のどちらも全回で空、managed と `synchronize` では全回で正しい値。UHD 630 はどちらも全回で正しい値。段階 0 の結果を本体と同じ読み戻しの手順で確かめた。両 device の GPU family は `mac2`、`common1`〜`3`、`metal3` で Apple の family はなく、`hasUnifiedMemory` は Radeon が false、UHD 630 が true。段階 0 のとおり shared の texture は非 Apple family では API validation で abort するので、**validation を無効にした状態の成功は、正式な GPU 互換性の保証ではない**。Metal Effects の維持方針と段階 5 の修正計画は変えない。
- **未確認**: OmniWM 本体の x86_64 テストビルド(テストターゲットは `OmniWMTests` の 1 つ)、固定 toolchain で作る一覧取得ツール、多数のテストを指定する実行、`--filter` / `--skip` の再現。

---

### 進め方の変更(2026-11-27、承認済み)

実機で使えることを優先し、「全テストの成功まで次の段階に進まない」というゲートはやめる。

1. 実機で確認した描画の不具合と Intel の温度センサーを直す(承認済みの Hidden Bar の 27 ゲートは、実機で起動する前に入れる)。
2. CI でビルドして実機に入れ、使いながら直す。
3. 自動テストは回帰の確認に使い、残る失敗は理由を記録する。

`origin` の `intel/` で始まる branch への commit、push、CI は都度の確認なしで行う。`main`、upstream、force push、PR の作成はしない。Intel 対応に必要な製品の修正は進めて報告する。テストの変更は環境や CPU の差と実証できたものに限り、理由を記録する。機能の無効化、実機への配置と起動、署名用の証明書の作成、権限の付与は事前に確認する。

### 実行時の修正(2026-11-27)

| 修正 | 内容 | 根拠と確認 |
|---|---|---|
| 描画の texture | `ShellShaderGPU.textureStorage` が Apple family では shared、それ以外では managed を選ぶ。CPU が読む前(`getBytes` と hit test)に blit の `synchronize` を入れる。5 箇所と、同じ texture を作るテスト 3 箇所 | 非 Apple family の texture に shared はない([shared](https://developer.apple.com/documentation/metal/mtlstoragemode/shared))。managed は GPU の書き込み後に同期が要る([同期](https://developer.apple.com/documentation/metal/synchronizing-a-managed-resource-in-macos))。実機の Radeon で shared の読み戻しが空、managed と同期で正しい値。IOSurface の texture は両 GPU で API validation を有効にして確認。Apple GPU の動作は変わらない |
| 温度センサー | Intel の CPU コア(`TC1C` など)と dGPU の die(`TGDD`、`TG0D`)を読み、`sp78` を解釈する。Apple silicon のキーと `flt ` はそのまま | 実機の全温度キーは `sp78`。新しいコードを実機で実行し、CPU の 8 コアと Radeon の値を取得。キーの意味は [exelban/stats](https://github.com/exelban/stats/blob/master/Modules/Sensors/values.swift) と一致。内蔵 GPU だけの Intel Mac では GPU の温度は空欄 |
| Hidden Bar | macOS 27 未満では preference の読み書きをせず `unavailable` にする(D2)。UI の文言は上流のまま 27 以上と説明する | 26 でシステム設定を書き換えないため |

app job を push で実行する状態に戻した。

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
