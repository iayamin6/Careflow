#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SPARK_HOME:?Set SPARK_HOME to your Apache Spark installation}"
mkdir -p spark/target/classes
java -Xmx1g -cp "$SPARK_HOME/jars/*" scala.tools.nsc.Main -usejavacp -d spark/target/classes spark/src/main/scala/careflow/WalletAnalytics.scala
jar cf spark/target/careflow-wallet.jar -C spark/target/classes .
