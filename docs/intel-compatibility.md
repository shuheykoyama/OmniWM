# OmniWM Intel Compatibility

## Objective

OmniWM v0.8.0 の機能・UXを最大限維持し、Intel Mac（x86_64、macOS 26）で利用できる互換版を作成する。独自の機能追加やUIの再設計は目的としない。比較基準は同じmacOSバージョン上の公式版とし、macOS 27専用機能のバックポートは行わない。

## Target Environment

| Item | Requirement |
| --- | --- |
| Initial upstream version | v0.8.0 |
| Initial upstream commit | `bf118cca1449468397dee3662acbba5544657545` |
| Target architecture | x86_64 |
| Deployment target | macOS 26.0 |
| Test machine | MacBook Pro (2019, 16-inch), Intel, macOS 26.7.3 (25G342) |
| Build strategy | Apple Silicon上のGitHub Actionsによるクロスコンパイルを第一候補とする。具体的なrunner・toolchainは調査で確定する |
| Security | SIPを有効にしたまま利用できること |

## Feature Requirements

「維持」は要求仕様であり、現時点でIntel上の動作を確認済みという意味ではない。

| Feature | Policy |
| --- | --- |
| Window management | 必須。Niri・Dwindle、ワークスペース、マルチディスプレイ、ウィンドウ操作、ルール、ショートカット等の既存機能を維持する |
| Quake Terminal | GhosttyKitを含めて維持する。外部ターミナルへの置き換えを前提としない |
| OmniShell | Luauランタイム、ウィジェット、スタイル等を維持する |
| Metal Effects | Intel Macでのコンパイル・描画・安定性を調査し、維持を試みる。対応できない場合に限り該当する効果を無効化する。コアの描画機能への影響は別途報告する |
| Apple Intelligence | 既存の非AI処理にフォールバックする。代替LLM・クラウドAPIは導入しない |
| Hidden Bar | 上流のOS要件を維持する。macOS 26へのバックポートは行わない |
| Updater | 上流の更新検知・通知を維持する。公式バイナリによる自動更新・アプリ内インストールは無効化し、Intel版は手動で再ビルド・更新する |
| Other features | 原則維持する。維持困難な機能が見つかった場合は、原因・影響・代替案を提示して承認を得る |

## Compatibility Policy

- 上流と同等のUI・UX、設定ファイル、CLI、IPCの互換性を可能な限り維持する。
- 非対応機能は単に初期値をOFFにするだけでなく、OS・CPU・GPU・APIの利用可否に応じて実行を安全に抑止する。
- 既存のavailability checkとフォールバックを優先し、非対応機能をUI・設定・CLI・IPCから要求されてもクラッシュしないこと。設定値を不要に破壊・初期化しない。
- 利用不能な機能は上流のUI慣例に沿って無効化または非表示にし、必要に応じて理由を示す。
- 機能のコードを恒久的に削除しない。コンパイル時に分離が必要な場合は最小限の条件分岐等を用いる。
- Metal Effectsの無効化は可能な限り個別の効果単位とし、OmniShellやウィンドウ管理全体の削除に波及させない。
- macOS 26の既存APIで実現できる機能を維持し、macOS 27専用機能は既存の制約に従う。

## Update Policy

- 上流の新バージョンを検知し通知できること。通知はIntel対応バイナリの提供を意味しない。
- 公式arm64バイナリのダウンロードからインストール、置き換え、更新用再起動に至る経路を安全に遮断する。手動で公式リリース情報を閲覧することは妨げない。
- Intel版の更新は、上流の新しい安定版を取り込み、互換パッチを適用し、再ビルド・検証して手動インストールする。
- 上流の最低OSがmacOS 27以上になった版は取り込まず、macOS 26対応の最後の版で固定する。
- 独自のアップデートサーバーや自動アップデーターは実装しない。
- 署名、Bundle ID、設定・権限の引き継ぎ、既存インストールとの衝突、更新・ロールバック手順は調査で決定する。

## Engineering Constraints

- 上流との差分を最小化し、将来の更新を容易にする。YAGNI、KISS、DRYを遵守する。
- 互換性とは無関係なリファクタリング、機能追加、依存関係の更新は行わない。
- 対処の優先順位は、既存実装の利用、ビルド設定の修正、既存フォールバック、最小限の互換実装、承認された機能の無効化とする。
- 可能な限り上流のツールチェーン・依存関係の固定バージョンを維持する。変更が必要なら理由と影響を明示する。
- 既存のarm64向けビルドを不必要に壊さない。Intel固有のCIやパッチは必要最小限にする。
- ローカルの開発環境や他のアプリへの不要な変更を避ける。秘密情報をリポジトリ・CIログに含めない。
- ghはGitHub操作、ghqはclone・リポジトリ管理、jjはVCS操作に使用する。作業者はgitコマンドを直接使用しない（既存CI・依存ツール内部のGit利用は別扱い）。
- originは `shuheykoyama/OmniWM`、upstreamは `OmniNull/OmniWM` とする。

## Acceptance Criteria

- CIでx86_64向けの再現可能なビルドが生成され、実行ファイルとロード対象の依存バイナリに必要なアーキテクチャが存在する。最低OS要件も確認できる。
- Intel実機のmacOS 26.7.3で起動し、SIPを無効にせず必要な権限を付与できる。
- ウィンドウ管理のコア機能、Quake Terminal、OmniShell、既存の設定・CLI・IPCが動作する。Metal Effectsの結果を個別に記録する。
- Apple Intelligence非対応時は非AI処理が動作し、Hidden Bar等のOS非対応機能が安全に扱われる。
- 更新通知は機能し、非対応の公式バイナリがインストールされない。手動更新・ロールバック手順が確認できる。
- 利用不能な機能や未検証事項は明示し、ビルド成功と実機動作成功を区別する。macOS 26.0全体の動作保証は26.7.3のみの実機検証から推定しない。

## Open Technical Decisions

具体的なSwift/Xcode/SDK、CI runner、GhosttyKitの生成方法、Private API・Metal・Luauの互換性、パッケージングと署名、Updaterの実装、上流更新の手順はソース監査・実験によって決定する。本書はそれらの実装方法を事前に固定しない。
