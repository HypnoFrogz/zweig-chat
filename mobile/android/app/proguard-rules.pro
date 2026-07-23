-keepattributes Signature,InnerClasses,EnclosingMethod

# Required by flutter_local_notifications: Gson TypeToken for scheduled notifications cache.
-keep class com.google.gson.reflect.TypeToken { *; }
-keep class * extends com.google.gson.reflect.TypeToken
