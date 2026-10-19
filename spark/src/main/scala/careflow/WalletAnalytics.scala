package careflow

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._
import org.apache.spark.sql.types._

/** Distributed, point-in-time event reconciliation and behavioral analytics.
  * No transaction-level collect; only bounded manifests reach the driver.
  */
object WalletAnalytics {
  def main(args: Array[String]): Unit = {
    require(args.length == 3, "Usage: WalletAnalytics <export-dir> <output-dir> <as-of-UTC>")
    val spark = SparkSession.builder.appName("CareFlow Wallet Analytics").getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.shuffle.partitions", "8")
    val input = args(0); val output = args(1); val at = to_timestamp(lit(args(2)))
    val schema = new StructType()
      .add("event_id", StringType).add("transaction_id", StringType).add("attempt_id", StringType)
      .add("customer_id", StringType).add("merchant_id", StringType).add("amount_minor", LongType)
      .add("channel", StringType).add("status", StringType).add("occurred_at", StringType)
      .add("received_at", StringType).add("latency_ms", LongType)
    val raw = spark.read.schema(schema).json(input + "/events.json.gz")
    val valid = raw.filter(to_timestamp(col("occurred_at")) <= at && to_timestamp(col("received_at")) <= at)
    require(valid.filter(col("amount_minor") <= 0 || col("event_id").isNull || col("transaction_id").isNull || col("attempt_id").isNull || !col("status").isin("pending","failed","timeout","succeeded","refunded","reversed")).limit(1).count() == 0, "Invalid payment data")
    val distinct = valid.dropDuplicates()
    require(distinct.groupBy("event_id").count().filter(col("count") > 1).limit(1).count() == 0, "Conflicting event identity")
    val events = distinct.dropDuplicates("event_id").withColumn("occurred",to_timestamp(col("occurred_at")))
      .withColumn("rank",when(col("status").isin("refunded","reversed"),3).when(col("status")==="succeeded",2).when(col("status").isin("failed","timeout"),1).otherwise(0)).cache()
    val attemptWindow = Window.partitionBy("attempt_id")
    val attempts = events.withColumn("started_at",min("occurred").over(attemptWindow))
      .withColumn("had_timeout",max(when(col("status")==="timeout",1).otherwise(0)).over(attemptWindow))
      .withColumn("position",row_number().over(attemptWindow.orderBy(col("rank").desc,col("occurred").desc,col("event_id").desc)))
      .filter(col("position")===1).cache()
    val txWindow = Window.partitionBy("transaction_id")
    val transactions = attempts.withColumn("tx_start",min("started_at").over(txWindow))
      .withColumn("position",row_number().over(txWindow.orderBy(col("rank").desc,col("occurred").desc,col("event_id").desc)))
      .filter(col("position")===1).drop("started_at").withColumnRenamed("tx_start","started_at").cache()
    val completed = col("status").isin("succeeded","refunded","reversed")
    def since(days: Int) = at - expr(s"INTERVAL $days DAYS")
    def behaviors(key: String, profiles: String): DataFrame = {
      val successful = transactions.filter(col("status")==="succeeded")
      val history = successful.groupBy(key).agg(
        sum(when(col("started_at")>since(30),1).otherwise(0)).as("frequency_30d"),
        sum(when(col("started_at")>since(60) && col("started_at")<=since(30),1).otherwise(0)).as("frequency_previous_30d"),
        sum(when(col("started_at")>since(30),col("amount_minor")).otherwise(0L)).as("monetary_minor_30d"),
        avg(when(col("started_at")>since(30),col("amount_minor"))).as("average_amount_minor"),
        countDistinct(when(col("started_at")>since(30),to_date(col("started_at")))).as("active_days_30d"),
        countDistinct(when(col("started_at")>since(30),col(if(key=="customer_id") "merchant_id" else "customer_id"))).as("counterparty_diversity_30d"),
        ((unix_timestamp(at)-unix_timestamp(max("started_at")))/86400.0).as("recency_days"))
      val reliability = attempts.filter(col("started_at")>since(7)).groupBy(key).agg(count(lit(1)).as("attempts_7d"),avg(when(completed,1.0).otherwise(0.0)).as("attempt_success_rate_7d"))
      val profile = spark.read.schema(new StructType().add("id",StringType)).json(input+s"/$profiles.json").withColumnRenamed("id",key)
      profile.join(history,Seq(key),"left").join(reliability,Seq(key),"left")
        .na.fill(0,Seq("frequency_30d","frequency_previous_30d","monetary_minor_30d","active_days_30d","counterparty_diversity_30d","attempts_7d"))
        .withColumn("frequency_change",when(col("frequency_previous_30d")>0,col("frequency_30d")/col("frequency_previous_30d")-1))
        .withColumn("segment",when(col("frequency_30d")===0,"inactive").when(col("frequency_change") <= -0.4,"declining_engagement").when(col("frequency_30d")>=20,"highly_active").otherwise("regular"))
        .withColumn("as_of",lit(args(2)))
    }
    def save(df: DataFrame, name: String): Unit = {
      df.write.mode("errorifexists").parquet(output+s"/$name.parquet")
      df.write.mode("errorifexists").json(output+s"/$name.json")
    }
    save(behaviors("customer_id","customers"),"customers")
    save(behaviors("merchant_id","merchants"),"merchants")
    val windows = Seq(1,7,30).map { days =>
      val a=attempts.filter(col("started_at")>since(days))
      val t=transactions.filter(col("started_at")>since(days))
      a.agg(count(lit(1)).as("attempts"),avg(when(completed,1.0).otherwise(0.0)).as("attempt_success_rate"),
        avg(when(col("status").isin("failed","timeout"),1.0).otherwise(0.0)).as("failure_rate"),
        avg(when(col("status")==="pending",1.0).otherwise(0.0)).as("pending_rate"),avg("had_timeout").as("timeout_rate"),
        expr("percentile_approx(latency_ms, 0.95, 10000)").as("latency_p95_ms"))
        .crossJoin(t.agg(count(lit(1)).as("transactions"),avg(when(completed,1.0).otherwise(0.0)).as("completion_rate"),
          avg(when(col("status").isin("refunded","reversed"),1.0).otherwise(0.0)).as("refund_reversal_rate")))
        .withColumn("days",lit(days)).withColumn("retry_rate",when(col("attempts")>0,(col("attempts")-col("transactions"))/col("attempts")))
    }.reduce(_.unionByName(_))
    save(windows,"reliability")
    transactions.withColumn("date",to_date(col("started_at"))).groupBy("date","channel","merchant_id")
      .agg(count(lit(1)).as("transactions"),sum(when(completed,1).otherwise(0)).as("completed"),sum(when(col("status")==="succeeded",col("amount_minor")).otherwise(0L)).as("successful_value_minor"))
      .write.partitionBy("date").mode("errorifexists").parquet(output+"/daily.parquet")
    import spark.implicits._
    Seq((raw.count(),events.count(),attempts.count(),transactions.count(),args(2),spark.version))
      .toDF("input_rows","unique_events","attempts","transactions","as_of","spark_version").write.json(output+"/manifest.json")
    spark.stop()
  }
}
