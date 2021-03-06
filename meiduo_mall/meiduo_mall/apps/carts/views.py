from django.shortcuts import render
from django_redis import get_redis_connection
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework import status
import pickle
import base64

from . import constants
from .serializers import CartSerializer, CartSKUSerializer, CartDeleteSerializer, CartSelectAllSerializer
from goods.models import SKU
# Create your views here.


class CartsView(GenericAPIView):
    """
    购物车视图(保存)
    """
    def perform_authentication(self, request):
        """
        将执行具体请求方法前的身份认证关掉，由视图自己来进行身份认证
        原因： 请求体中用于身份认证的头信息中默认先使用JWT验证(setting设置),会带有“Authorization”:'JWT'+ tokken
        如果用户未登录这个token就是错误的，那么执行JWT身份验证时就会报错。我们可以重写APIview的initionl内的perform_authentication方法
        这样就不会执行JWT验证，而是用接下来的session验证
        """
        pass

    serializer_class = CartSerializer
    def post(self, request):
        # sku_id  count  selected
        # 校验
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        sku_id = serializer.validated_data['sku_id']
        count = serializer.validated_data['count']
        selected = serializer.validated_data['selected']

        # 判断用户登录状态
        try:
            user = request.user   # 匿名用户 AnonymoseUser
        except Exception:
            user = None

        # 保存
        if user and user.is_authenticated:
            # 如果用户已登录，保存到redis
            redis_conn = get_redis_connection('cart')
            pl = redis_conn.pipeline()

            # 用户购物车数据  redis hash哈希
            pl.hincrby('cart_%s' % user.id, sku_id, count)

            # 用户购物车勾选数据  redis  set
            if selected:
                pl.sadd('cart_selected_%s' % user.id, sku_id)

            pl.execute()
            return Response(serializer.data)

        else:
            # 如果用户未登录，保存到cookie  reponse = Response() response.set_cookie
            # 取出cookie中的购物车数据
            cart_str = request.COOKIES.get('cart')

            if cart_str:
                # 解析
                cart_str = cart_str.encode()  # str -> bytes
                cart_bytes = base64.b64decode(cart_str)  # b64decode(byes类型）
                cart_dict = pickle.loads(cart_bytes)
            else:
                cart_dict = {}

            # cart_dict = {
            #     sku_id_1: {
            #         'count': 10
            #         'selected': True
            #     },
            #     sku_id_2: {
            #         'count': 10
            #         'selected': False
            #     },
            #     sku_id_3: {
            #         'count': 10
            #         'selected': True
            #     }
            # }

            if sku_id in cart_dict:
                # 如果商品存在购物车中，累加
                cart_dict[sku_id]['count'] += count
                cart_dict[sku_id]['selected'] = selected
            else:
                # 如果商品不在购物车中，设置
                cart_dict[sku_id] = {
                    'count': count,
                    'selected': selected
                }

            cart_cookie = base64.b64encode(pickle.dumps(cart_dict)).decode()

            # 设置cookie
            response = Response(serializer.data)
            response.set_cookie('cart', cart_cookie, max_age=constants.CART_COOKIE_EXPIRES)

            return response

    def get(self, request):
        """ 查询购物车 """

        # 判断用户登录状态
        try:
            user = request.user
        except Exception:
            user = None

        if user is not None and user.is_authenticated:
            # 用户已登录，从redis中读取 sku, count, selected
            redis_conn = get_redis_connection('cart')
            redis_cart = redis_conn.hgetall('cart_%s' % user.id)
            redis_cart_selected = redis_conn.smembers('cart_selected_%s' % user.id)
            cart = {}
            for sku_id, count in redis_cart.items():
                # sku_id, count  redis取出来时bytes类型
                cart[int(sku_id)] = {
                    'count': int(count),
                    'selected': sku_id in redis_cart_selected
                }
        else:
            # 用户未登录，从cookie中读取
            cart = request.COOKIES.get('cart')
            if cart is not None:
                cart = pickle.loads(base64.b64decode(cart.encode())) # str-->bytes-->b64
            else:
                cart = {}

        # 遍历处理购物车数据
        skus = SKU.objects.filter(id__in=cart.keys())
        # 遍历sku_obj_list 向sku对象中添加count和selected属性
        for sku in skus:
            sku.count = cart[sku.id]['count']
            sku.selected = cart[sku.id]['selected']

        serializer = CartSKUSerializer(skus, many=True)
        return Response(serializer.data)
    
    def put(self, request):
        """
        修改购物车数据
        """
        # 校验，和添加购物车相同
        serializer = CartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sku_id = serializer.validated_data.get('sku_id')
        count = serializer.validated_data.get('count')
        selected = serializer.validated_data.get('selected')

        # 尝试对请求的用户进行验证
        try:
            user = request.user
        except Exception:
            # 验证失败，用户未登录
            user = None

        if user is not None and user.is_authenticated:
            # 用户已登录，在redis中保存
            redis_conn = get_redis_connection('cart')
            pl = redis_conn.pipeline()
            # 处理数量
            pl.hset('cart_%s' % user.id, sku_id, count)
            # 处理勾选状态
            if selected:  # 勾选
                pl.sadd('cart_selected_%s' % user.id, sku_id)
            else:  # 取消勾选
                pl.srem('cart_selected_%s' % user.id, sku_id)
            pl.execute()
            return Response(serializer.data)
        else:
            # 用户未登录，在cookie中保存
            # 使用pickle序列化购物车数据，pickle操作的是bytes类型
            cart = request.COOKIES.get('cart')
            if cart is not None:
                cart = pickle.loads(base64.b64decode(cart.encode()))
            else:
                cart = {}

            response = Response(serializer.data)

            if sku_id in cart:
                cart[sku_id] = {
                    'count': count,
                    'selected': selected
                }
                cookie_cart = base64.b64encode(pickle.dumps(cart)).decode()

                # 设置购物车的cookie
                # 需要设置有效期，否则是临时cookie
                response.set_cookie('cart', cookie_cart, max_age=constants.CART_COOKIE_EXPIRES)
            return response
    
    def delete(self, request):
        """
        删除购物车数据
        """
        serializer = CartDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sku_id = serializer.validated_data['sku_id']

        try:
            user = request.user
        except Exception:
            # 验证失败，用户未登录
            user = None

        if user is not None and user.is_authenticated:
            # 用户已登录，在redis中保存
            redis_conn = get_redis_connection('cart')
            pl = redis_conn.pipeline()
            pl.hdel('cart_%s' % user.id, sku_id)
            pl.srem('cart_selected_%s' % user.id, sku_id)
            pl.execute()
            return Response(status=status.HTTP_204_NO_CONTENT)
        else:
            # 用户未登录，在cookie中保存
            response = Response(status=status.HTTP_204_NO_CONTENT)

            # 使用pickle序列化购物车数据，pickle操作的是bytes类型
            cart = request.COOKIES.get('cart')
            if cart is not None:
                cart = pickle.loads(base64.b64decode(cart.encode()))
                if sku_id in cart:
                    del cart[sku_id]
                    cookie_cart = base64.b64encode(pickle.dumps(cart)).decode()
                    # 设置购物车的cookie
                    # 需要设置有效期，否则是临时cookie
                    response.set_cookie('cart', cookie_cart, max_age=constants.CART_COOKIE_EXPIRES)
            return response



class CartSelectAllView(GenericAPIView):
    """
    购物车全选
    """
    serializer_class = CartSelectAllSerializer

    def perform_authentication(self, request):
        """
        重写父类的用户验证方法，不在进入视图前就检查JWT
        """
        pass

    def put(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        selected = serializer.validated_data['selected']

        try:
            user = request.user
        except Exception:
            # 验证失败，用户未登录
            user = None

        if user is not None and user.is_authenticated:
            # 用户已登录，在redis中保存
            redis_conn = get_redis_connection('cart')
            cart = redis_conn.hgetall('cart_%s' % user.id)
            sku_id_list = cart.keys()

            if selected:
                # 全选, 所有的sku_id都添加到redis set
                redis_conn.sadd('cart_selected_%s' % user.id, *sku_id_list)
            else:
                # 取消全选 清空redis中set数据
                redis_conn.srem('cart_selected_%s' % user.id, *sku_id_list)
            return Response({'message': 'OK'})
        else:
            # cookie
            cart = request.COOKIES.get('cart')

            response = Response({'message': 'OK'})

            if cart is not None:
                cart = pickle.loads(base64.b64decode(cart.encode()))
                for sku_id in cart:
                    cart[sku_id]['selected'] = selected
                cookie_cart = base64.b64encode(pickle.dumps(cart)).decode()
                # 设置购物车的cookie
                # 需要设置有效期，否则是临时cookie
                response.set_cookie('cart', cookie_cart, max_age=constants.CART_COOKIE_EXPIRES)

            return response
