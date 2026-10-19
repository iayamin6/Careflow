ThisBuild / scalaVersion := "2.13.16"
ThisBuild / version := "0.2.0"
libraryDependencies += "org.apache.spark" %% "spark-sql" % "4.0.3" % "provided"
Compile / mainClass := Some("careflow.WalletAnalytics")
